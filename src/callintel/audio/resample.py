"""Streaming polyphase FIR resampler (numpy only).

Telephony audio arrives at 8 kHz; Whisper expects 16 kHz. Upsampling adds no information
(the band above 4 kHz stays empty); it is purely a format conversion. MP3s from other sources
may be 44.1/48 kHz and need real anti-aliased downsampling, so this is a general L/M resampler.

It is streaming so that a multi-hour recording is processed in fixed memory: push() blocks
of frames and get back every output sample whose filter support is already available.
The design mirrors scipy.signal.resample_poly (Kaiser-windowed sinc, zero-phase alignment).
"""

import math
from functools import lru_cache

import numpy as np

_STEP = 16_384  # outputs computed per vectorized step; bounds temporary memory


@lru_cache(maxsize=16)
def _design(up: int, down: int) -> tuple[np.ndarray, int]:
    max_rate = max(up, down)
    half = 10 * max_rate
    n = np.arange(-half, half + 1, dtype=np.float64)
    cutoff = 1.0 / max_rate  # fraction of the upsampled Nyquist
    h = cutoff * np.sinc(cutoff * n) * np.kaiser(2 * half + 1, 5.0)
    h *= up / h.sum()  # unity DC gain after polyphase decimation
    return h.astype(np.float32), half


class StreamingResampler:
    """Resamples (frames, channels) float32 blocks from sr_in to sr_out."""

    def __init__(self, sr_in: int, sr_out: int, channels: int) -> None:
        g = math.gcd(sr_in, sr_out)
        self.up, self.down = sr_out // g, sr_in // g
        self.channels = channels
        self.passthrough = self.up == self.down
        if not self.passthrough:
            self._h, self._half = _design(self.up, self.down)
            self._taps = (2 * self._half) // self.up + 2
        self._buf = np.zeros((0, channels), np.float32)
        self._buf_start = 0  # absolute input index of _buf[0]
        self._n_in = 0  # input frames seen so far
        self._next = 0  # next output index to produce

    def push(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, np.float32).reshape(-1, self.channels)
        if self.passthrough:
            return block
        self._buf = np.concatenate([self._buf, block])
        self._n_in += len(block)
        # Output k needs inputs up to floor((k*down + half) / up); it is ready once that < n_in.
        ready = max(self._next, -(-(self._n_in * self.up - self._half) // self.down))
        out = self._compute(self._next, ready)
        self._next = ready
        self._trim()
        return out

    def flush(self) -> np.ndarray:
        """Emit the tail, treating samples past the end of the input as zeros."""
        if self.passthrough:
            return np.zeros((0, self.channels), np.float32)
        total = -(-self._n_in * self.up // self.down)
        out = self._compute(self._next, total)
        self._next = total
        return out

    def _trim(self) -> None:
        first_needed = max(0, -(-(self._next * self.down - self._half) // self.up))
        drop = first_needed - self._buf_start
        if drop > 0:
            self._buf = self._buf[drop:]
            self._buf_start = first_needed

    def _compute(self, k0: int, k1: int) -> np.ndarray:
        if k1 <= k0 or len(self._buf) == 0:
            return np.zeros((max(0, k1 - k0), self.channels), np.float32)
        h, half, up = self._h, self._half, self.up
        chunks = []
        for a in range(k0, k1, _STEP):
            k = np.arange(a, min(a + _STEP, k1), dtype=np.int64)
            t = k * self.down  # output position on the upsampled grid
            n0 = -(-(t - half) // up)  # first contributing input index
            idx = n0[:, None] + np.arange(self._taps)[None, :]
            ci = t[:, None] - idx * up + half
            valid = (ci >= 0) & (ci < len(h)) & (idx >= 0) & (idx < self._n_in)
            coef = np.where(valid, h[np.clip(ci, 0, len(h) - 1)], np.float32(0))
            vals = self._buf[np.clip(idx - self._buf_start, 0, len(self._buf) - 1)]
            chunks.append(np.einsum("kt,ktc->kc", coef, vals, optimize=True))
        return np.concatenate(chunks).astype(np.float32, copy=False)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """One-shot convenience wrapper. Accepts mono (n,) or multi-channel (n, c) input."""
    mono = x.ndim == 1
    frames = x.reshape(len(x), -1)
    rs = StreamingResampler(sr_in, sr_out, frames.shape[1])
    out = np.concatenate([rs.push(frames), rs.flush()])
    return out[:, 0] if mono else out
