"""faster-whisper (CTranslate2) backend.

Inference is CPU-bound native code. Running it on the event loop would block AMQP
heartbeats; RabbitMQ would then drop the connection mid-call and redeliver the message
forever. So the model lives in a dedicated process, loaded once, and the event loop only
awaits a future. A timeout or a dead child process tears the pool down and starts a new one.
"""

import asyncio
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from callintel.asr.base import ASRRequest, Segment
from callintel.config import Settings
from callintel.errors import TransientError

log = logging.getLogger(__name__)

_model: Any = None  # per child process


def _load(model: str, compute_type: str, cpu_threads: int) -> None:
    global _model
    from faster_whisper import WhisperModel

    _model = WhisperModel(model, device="cpu", compute_type=compute_type, cpu_threads=cpu_threads)


def _transcribe(path: str, beam_size: int) -> list[tuple[float, float, str, float]]:
    import soundfile as sf

    # Preprocessing already produced 16 kHz mono PCM, so decode it ourselves and hand
    # faster-whisper an array. Its own decoding goes through PyAV, whose API broke in
    # PyAV 15 ("unexpected keyword argument 'metadata_errors'").
    audio, _sr = sf.read(path, dtype="float32")
    segments, _info = _model.transcribe(audio, language="fa", beam_size=beam_size,
                                        vad_filter=True, condition_on_previous_text=False)
    return [(s.start, s.end, s.text.strip(), s.avg_logprob) for s in segments]


class WhisperASR:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: ProcessPoolExecutor | None = None

    @property
    def name(self) -> str:
        s = self._settings
        return f"faster-whisper/{s.whisper_model}-{s.whisper_compute_type}"

    async def start(self) -> None:
        self._pool = self._new_pool()
        # Warm up: load the model now, not inside the first message's timeout budget.
        await asyncio.get_running_loop().run_in_executor(self._pool, _noop)
        log.info("whisper model loaded", extra={"model": self.name})

    def _new_pool(self) -> ProcessPoolExecutor:
        s = self._settings
        return ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_load,
            initargs=(s.whisper_model, s.whisper_compute_type, s.whisper_cpu_threads),
        )

    def _reset(self) -> None:
        if self._pool is not None:
            # Python < 3.14 has no public terminate; a stuck CTranslate2 call must be killed.
            for proc in list(getattr(self._pool, "_processes", {}).values()):
                proc.terminate()
            self._pool.shutdown(wait=False, cancel_futures=True)
        self._pool = self._new_pool()

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)

    async def transcribe(self, audio: Path, request: ASRRequest) -> list[Segment]:
        assert self._pool is not None
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(self._pool, _transcribe, str(audio),
                                             self._settings.whisper_beam_size)
        except asyncio.CancelledError:
            self._reset()  # the worker's timeout fired; kill the busy child
            raise
        except BrokenProcessPool as exc:
            self._reset()
            raise TransientError("ASR process died") from exc
        return [Segment(start, end, text, confidence=logprob) for start, end, text, logprob in raw]


def _noop() -> None:
    return None
