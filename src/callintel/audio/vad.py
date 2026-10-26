"""Energy-based voice activity detection and conversation-level audio features.

Deliberately simple and model-free: it runs per channel on the normalized 16 kHz audio and
gives the QA rules ASR-independent signals (holds, dead air, interruptions, talk ratio).
"""

import itertools
from typing import Any

import numpy as np

FRAME_S = 0.02


class FrameEnergy:
    """Accumulates per-frame RMS energy (dBFS) from a stream of mono samples."""

    def __init__(self, sample_rate: int, frame_s: float = FRAME_S) -> None:
        self.frame = round(sample_rate * frame_s)
        self._rest = np.zeros(0, np.float32)
        self._chunks: list[np.ndarray] = []

    def push(self, samples: np.ndarray) -> None:
        x = np.concatenate([self._rest, np.asarray(samples, np.float32)])
        n = len(x) // self.frame
        if n:
            frames = x[: n * self.frame].reshape(n, self.frame)
            rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
            self._chunks.append((20 * np.log10(rms)).astype(np.float32))
        self._rest = x[n * self.frame :]

    def finish(self) -> np.ndarray:
        if len(self._rest) >= self.frame // 2:
            self.push(np.zeros(self.frame - len(self._rest), np.float32))
        return np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True values."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(),
                    strict=True))


def speech_mask(db: np.ndarray, *, floor_db: float = -45.0, margin_db: float = 12.0,
                close_frames: int = 15, min_frames: int = 8) -> np.ndarray:
    """Adaptive threshold (noise floor + margin), then fill short gaps and drop short blips.

    The threshold is capped below the loud level so a channel that is almost all speech
    (no quiet frames to estimate the noise floor from) is not misread as all noise.
    """
    if db.size == 0:
        return np.zeros(0, bool)
    noise, loud = np.percentile(db, [5, 95])
    threshold = max(floor_db, min(float(noise) + margin_db, float(loud) - 10.0))
    mask = db > threshold
    speech = runs(mask)
    for (_, end), (start, _) in itertools.pairwise(speech):
        if start - end < close_frames:
            mask[end:start] = True
    for start, end in runs(mask):
        if end - start < min_frames:
            mask[start:end] = False
    return mask


def compute_features(masks: list[np.ndarray], speakers: list[str],
                     frame_s: float = FRAME_S) -> dict[str, Any]:
    n = max((len(m) for m in masks), default=0)
    active = np.zeros((len(masks), n), bool)
    for i, m in enumerate(masks):
        active[i, : len(m)] = m
    anyone = active.any(axis=0)

    talk = {spk: round(float(active[i].sum()) * frame_s, 2) for i, spk in enumerate(speakers)}
    total_talk = sum(talk.values()) or 1.0
    features: dict[str, Any] = {
        "frame_s": frame_s,
        "duration_s": round(n * frame_s, 2),
        "talk_time_s": talk,
        "talk_ratio": {spk: round(t / total_talk, 3) for spk, t in talk.items()},
        "silence_ratio": 1.0,
        "longest_silence_s": 0.0,
        "silences": [],
        "overlap_ratio": 0.0,
        "overlap_events": [],
    }
    speech = runs(anyone)
    if not speech:
        return features

    # Silence *inside* the conversation only; leading/trailing line noise is not dead air.
    first, last = speech[0][0], speech[-1][1]
    gaps = [(end, start) for (_, end), (start, _) in itertools.pairwise(speech)]
    silences = [
        {"start_s": round(a * frame_s, 2), "end_s": round(b * frame_s, 2),
         "duration_s": round((b - a) * frame_s, 2)}
        for a, b in gaps if (b - a) * frame_s >= 2.0
    ]
    features["silence_ratio"] = round(float((~anyone[first:last]).mean()), 3)
    features["longest_silence_s"] = round(max(((b - a) * frame_s for a, b in gaps), default=0), 2)
    features["silences"] = silences[:100]

    if len(masks) == 2:
        both = active[0] & active[1]
        min_overlap = round(0.3 / frame_s)
        events = [(a, b) for a, b in runs(both) if b - a >= min_overlap]
        features["overlap_ratio"] = round(float(both.sum()) / max(1, int(anyone.sum())), 3)
        features["overlap_events"] = [
            {"start_s": round(a * frame_s, 2), "end_s": round(b * frame_s, 2)}
            for a, b in events[:200]
        ]
    return features
