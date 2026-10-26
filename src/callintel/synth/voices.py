"""Voice backends: Microsoft Edge neural TTS (Persian voices) or an offline babble synth.

The offline backend produces speech-shaped audio with the right timing and energy envelope.
It exercises the whole pipeline (VAD, holds, interruptions, throughput) with the mock ASR,
without any network access.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

from callintel.audio.resample import resample

SR = 8000


@dataclass(frozen=True)
class VoiceStyle:
    voice: str  # Edge voice name, e.g. fa-IR-FaridNeural
    rate: str = "+0%"
    pitch: str = "+0Hz"
    f0: float = 140.0  # offline synth only


class VoiceBackend(Protocol):
    name: str

    async def render(self, text: str, style: VoiceStyle) -> np.ndarray:
        """Mono float32 at 8 kHz."""
        ...


def _key(text: str, style: VoiceStyle) -> str:
    raw = f"{style.voice}|{style.rate}|{style.pitch}|{text}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]


class EdgeVoices:
    name = "edge-tts"

    def __init__(self, cache_dir: Path, concurrency: int = 4) -> None:
        self._cache = cache_dir
        self._cache.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(concurrency)
        self._locks: dict[str, asyncio.Lock] = {}

    async def render(self, text: str, style: VoiceStyle) -> np.ndarray:
        key = _key(text, style)
        path = self._cache / f"{key}.mp3"
        # A sentence can occur twice in one call; fetch it once.
        async with self._locks.setdefault(key, asyncio.Lock()):
            if not path.exists():
                import edge_tts  # optional dependency (extra "synth")

                async with self._sem:
                    part = path.with_suffix(".part")
                    await edge_tts.Communicate(text, style.voice, rate=style.rate,
                                               pitch=style.pitch).save(str(part))
                    part.rename(path)
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return trim_silence(resample(data.mean(axis=1), sr, SR).astype(np.float32))


def trim_silence(x: np.ndarray, *, threshold_db: float = -45.0, pad_s: float = 0.05) -> np.ndarray:
    """TTS clips carry up to ~1 s of padding; scripted timings (and interruptions) must refer
    to the speech itself."""
    frame = int(0.01 * SR)
    n = len(x) // frame
    if n == 0:
        return x
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    active = np.flatnonzero(20 * np.log10(rms) > threshold_db)
    if active.size == 0:
        return x
    pad = int(pad_s * SR)
    return x[max(0, active[0] * frame - pad): min(len(x), (active[-1] + 1) * frame + pad)]


class SyntheticVoices:
    name = "synthetic"

    async def render(self, text: str, style: VoiceStyle) -> np.ndarray:
        return babble(text, f0=style.f0, seed=int(_key(text, style)[:8], 16))


def babble(text: str, *, f0: float, seed: int, sr: int = SR) -> np.ndarray:
    rng = np.random.default_rng(seed)
    letters = sum(ch.isalpha() for ch in text)
    duration = float(np.clip(0.075 * letters + 0.25, 0.6, 15.0))
    n = int(duration * sr)
    t = np.arange(n) / sr
    contour = 1 + 0.06 * np.sin(2 * np.pi * rng.uniform(0.3, 0.9) * t + rng.uniform(0, 6.3))
    phase = 2 * np.pi * np.cumsum(f0 * contour) / sr
    voiced = sum(0.6**k * np.sin(k * phase) for k in range(1, 20) if k * f0 < 3400)
    syllables = (0.5 * (1 - np.cos(2 * np.pi * rng.uniform(3.8, 5.2) * t))) ** 0.7
    words = max(1, len(text.split()))
    for boundary in np.linspace(0, n, words + 1)[1:-1]:
        syllables[int(boundary): int(boundary + 0.05 * sr)] *= 0.15
    fade = min(n // 2, int(0.02 * sr))
    syllables[:fade] *= np.linspace(0, 1, fade)
    syllables[n - fade:] *= np.linspace(1, 0, fade)
    signal = voiced / np.max(np.abs(voiced)) * syllables * 0.35
    signal += rng.normal(0, 0.01, n) * syllables  # breathiness
    return signal.astype(np.float32)


AGENT_STYLES = [
    VoiceStyle("fa-IR-FaridNeural", rate="+5%", f0=120.0),
    VoiceStyle("fa-IR-DilaraNeural", rate="+0%", f0=210.0),
]
CUSTOMER_STYLES = {
    "fa-IR-FaridNeural": [VoiceStyle("fa-IR-DilaraNeural", rate="-5%", pitch="-4Hz", f0=200.0),
                          VoiceStyle("fa-IR-DilaraNeural", rate="+8%", pitch="+6Hz", f0=230.0)],
    "fa-IR-DilaraNeural": [VoiceStyle("fa-IR-FaridNeural", rate="-3%", pitch="-6Hz", f0=105.0),
                           VoiceStyle("fa-IR-FaridNeural", rate="+6%", pitch="+5Hz", f0=135.0)],
}
