"""Oracle ASR for development, load and chaos testing.

Synthetic calls ship with ground-truth transcripts. This backend looks them up by the
upload's SHA-256, then behaves like a real model: it takes time proportional to the audio
(MOCK_ASR_RTF), can inject word errors (MOCK_ASR_WER) and random transient failures
(MOCK_ASR_FAILURE_RATE). A ground-truth file may also request a hard crash, which simulates
native inference code taking the whole process down (segfault / OOM kill).
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

from callintel.asr.base import ASRRequest, Segment
from callintel.config import Settings
from callintel.errors import TransientError

log = logging.getLogger(__name__)

_FILLER = ("خب", "یعنی", "اینکه", "آره", "ببینید", "الان")


class MockASR:
    def __init__(self, settings: Settings) -> None:
        self._dir = Path(settings.ground_truth_dir)
        self._rtf = settings.mock_asr_rtf
        self._wer = settings.mock_asr_wer
        self._failure_rate = settings.mock_asr_failure_rate
        self._index: dict[str, Path] = {}
        self._indexed_at = 0.0

    @property
    def name(self) -> str:
        return f"mock-oracle/wer{self._wer:g}"

    async def start(self) -> None:
        self._reindex()

    async def close(self) -> None:
        pass

    def _reindex(self) -> None:
        index = {}
        for path in self._dir.rglob("ground_truth/*.json"):
            try:
                index[json.loads(path.read_text())["sha256"]] = path
            except (OSError, ValueError, KeyError):
                log.warning("skipping unreadable ground truth", extra={"path": str(path)})
        self._index = index
        self._indexed_at = time.monotonic()
        log.info("ground truth indexed", extra={"files": len(index), "dir": str(self._dir)})

    def _lookup(self, sha256: str) -> dict[str, Any] | None:
        if sha256 not in self._index and time.monotonic() - self._indexed_at > 1.0:
            self._reindex()  # data may have been generated after startup
        path = self._index.get(sha256)
        return json.loads(path.read_text()) if path else None

    async def transcribe(self, audio: Path, request: ASRRequest) -> list[Segment]:
        truth = self._lookup(request.sha256)
        await asyncio.sleep(request.duration_s * self._rtf)
        if truth is None:
            log.warning("no ground truth for upload; returning empty transcript")
            return []
        if truth.get("inject", {}).get("asr") == "crash":
            log.critical("injected crash: terminating process without acking")
            os._exit(70)
        if random.random() < self._failure_rate:
            raise TransientError("injected transient ASR failure")

        rng = random.Random(f"{request.sha256}:{request.channel}")
        mono = truth.get("channels", 2) == 1
        return [
            Segment(s["start"], s["end"], corrupt(s["text"], self._wer, rng), confidence=None)
            for s in truth["segments"]
            if mono or s["channel"] == request.channel
        ]



def corrupt(text: str, wer: float, rng: random.Random) -> str:
    """Inject word errors at roughly `wer`: equal parts deletion, substitution, insertion."""
    if wer <= 0:
        return text
    out = []
    for word in text.split():
        roll = rng.random()
        if roll >= wer:
            out.append(word)
        elif roll < wer / 3:
            continue  # deletion
        elif roll < 2 * wer / 3:
            out.append(rng.choice(_FILLER))  # substitution
        else:
            out.extend([word, rng.choice(_FILLER)])  # insertion
    return " ".join(out)
