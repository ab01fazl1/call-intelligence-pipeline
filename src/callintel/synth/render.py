"""Assemble a two-party call and degrade it like a telephone line."""

import asyncio
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from callintel.audio.resample import resample
from callintel.synth.scenarios import AGENT, Scenario
from callintel.synth.voices import SR, VoiceBackend, VoiceStyle


@dataclass(frozen=True)
class RenderedCall:
    audio: np.ndarray  # (frames, 2): ch0 agent, ch1 customer, 8 kHz
    segments: list[dict[str, Any]]

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SR


async def render_call(scenario: Scenario, voices: VoiceBackend,
                      styles: dict[str, VoiceStyle], rng: random.Random) -> RenderedCall:
    clips = await asyncio.gather(*(voices.render(t.text, styles[t.speaker])
                                   for t in scenario.turns))
    cursor = rng.uniform(0.4, 0.9)
    prev_end: float | None = None
    placed = []
    for turn, clip in zip(scenario.turns, clips, strict=True):
        start = cursor
        if turn.overlap_s and prev_end is not None:
            start = max(0.0, prev_end - turn.overlap_s)
        end = start + len(clip) / SR
        placed.append((turn, clip, start, end))
        prev_end = max(prev_end or 0.0, end)
        cursor = prev_end + rng.uniform(0.25, 0.7) + turn.hold_after_s

    audio = np.zeros((int((cursor + 0.6) * SR) + 1, 2), np.float32)
    segments = []
    for turn, clip, start, end in placed:
        channel = 0 if turn.speaker == AGENT else 1
        i = int(start * SR)
        audio[i: i + len(clip), channel] += clip
        segments.append({"speaker": turn.speaker, "channel": channel, "start": round(start, 2),
                         "end": round(end, 2), "text": turn.text})
    return RenderedCall(telephone_line(audio, np.random.default_rng(rng.getrandbits(32))),
                        segments)


@lru_cache(maxsize=1)
def _bandpass(taps: int = 129) -> np.ndarray:
    n = np.arange(taps) - (taps - 1) / 2
    window = np.hamming(taps)

    def lowpass(cutoff_hz: float) -> np.ndarray:
        fc = 2 * cutoff_hz / SR
        return fc * np.sinc(fc * n) * window

    return (lowpass(3400) - lowpass(300)).astype(np.float32)


def _mulaw_roundtrip(x: np.ndarray, mu: float = 255.0) -> np.ndarray:
    """G.711 mu-law companding + 8-bit quantization, the classic telephony codec."""
    y = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    q = np.round((y + 1) / 2 * 255) / 255 * 2 - 1
    return (np.sign(q) * np.expm1(np.abs(q) * np.log1p(mu)) / mu).astype(np.float32)


def telephone_line(audio: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = np.empty_like(audio)
    t = np.arange(len(audio)) / SR
    for ch in range(audio.shape[1]):
        x = np.convolve(audio[:, ch], _bandpass(), mode="same")
        x += rng.normal(0, 10 ** (-54 / 20), len(x))  # line hiss
        x += 10 ** (-60 / 20) * np.sin(2 * np.pi * 50 * t)  # mains hum
        out[:, ch] = _mulaw_roundtrip(np.clip(x, -1, 1))
    return out


def write_audio(base: Path, call: RenderedCall, fmt: str) -> Path:
    """Write in one of the formats a PBX export might produce; returns the file path."""
    if fmt == "wav_pcm16_8k_stereo":
        path = base.with_suffix(".wav")
        sf.write(str(path), call.audio, SR, subtype="PCM_16")
    elif fmt == "wav_ulaw_8k_stereo":
        path = base.with_suffix(".wav")
        sf.write(str(path), call.audio, SR, subtype="ULAW")
    elif fmt == "mp3_16k_mono":
        mono = resample(call.audio.mean(axis=1), SR, 16_000)
        if "MP3" in sf.available_formats():
            path = base.with_suffix(".mp3")
            sf.write(str(path), mono, 16_000, format="MP3", subtype="MPEG_LAYER_III",
                     bitrate_mode="CONSTANT", compression_level=0.5)
        else:  # libsndfile built without LAME
            path = base.with_suffix(".wav")
            sf.write(str(path), mono, 16_000, subtype="PCM_16")
    else:
        raise ValueError(f"unknown format {fmt}")
    return path
