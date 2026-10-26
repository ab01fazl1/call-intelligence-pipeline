"""Decode -> validate -> split channels -> resample to 16 kHz -> VAD features.

Pure function over files so it can be unit-tested without any infrastructure. Memory is
bounded by the block size, not the recording length.
"""

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from callintel.audio.resample import StreamingResampler
from callintel.audio.vad import FRAME_S, FrameEnergy, compute_features, speech_mask
from callintel.errors import CorruptAudioError, UnsupportedAudioError

TARGET_SR = 16_000
BLOCK_S = 10
# libsndfile's MP3 decoder occasionally drops a frame at read boundaries; read compressed
# input in larger blocks (still bounded: 120 s of 48 kHz stereo float32 is ~46 MB).
COMPRESSED_BLOCK_S = 120
VERSION = "pp-16k-vad1"


@dataclass(frozen=True)
class PreprocessResult:
    duration_s: float
    source_sample_rate: int
    channels: int
    source_format: str
    outputs: list[Path]
    features: dict[str, Any]


def speakers_for(channels: int, channel_map: list[str]) -> list[str]:
    # PBX stereo recordings put each party on its own channel: free, perfect diarization.
    # Mono recordings have both parties mixed, so the speaker is unknown.
    return list(channel_map[:2]) if channels == 2 else ["unknown"]


def preprocess_file(src: Path, out_dir: Path, *, channel_map: list[str],
                    min_duration_s: float, max_duration_s: float) -> PreprocessResult:
    try:
        info = sf.info(str(src))
    except RuntimeError as exc:
        raise CorruptAudioError(f"unreadable audio: {exc}") from exc
    if info.channels not in (1, 2):
        raise UnsupportedAudioError(f"expected mono or stereo, got {info.channels} channels")
    if info.samplerate < 8000:
        raise UnsupportedAudioError(f"sample rate {info.samplerate} Hz is below telephony rate")
    if info.frames > 0 and info.duration > max_duration_s:
        raise UnsupportedAudioError(f"call is {info.duration:.0f}s, limit is {max_duration_s:.0f}s")

    channels = info.channels
    resampler = StreamingResampler(info.samplerate, TARGET_SR, channels)
    energies = [FrameEnergy(TARGET_SR) for _ in range(channels)]
    outputs = [out_dir / f"ch{i}.wav" for i in range(channels)]
    frames_in = 0

    try:
        with sf.SoundFile(str(src)) as reader, ExitStack() as stack:
            writers = [
                stack.enter_context(sf.SoundFile(str(p), "w", samplerate=TARGET_SR, channels=1,
                                                 format="WAV", subtype="PCM_16"))
                for p in outputs
            ]

            def emit(block: np.ndarray) -> None:
                block = np.clip(block, -1.0, 1.0)
                for i in range(channels):
                    writers[i].write(block[:, i])
                    energies[i].push(block[:, i])

            block_s = COMPRESSED_BLOCK_S if info.format == "MP3" else BLOCK_S
            for block in reader.blocks(blocksize=info.samplerate * block_s, dtype="float32",
                                       always_2d=True):
                frames_in += len(block)
                if frames_in / info.samplerate > max_duration_s:
                    raise UnsupportedAudioError(f"call exceeds {max_duration_s:.0f}s")
                emit(resampler.push(block))
            emit(resampler.flush())
    except RuntimeError as exc:  # soundfile.LibsndfileError
        raise CorruptAudioError(f"decode failed after {frames_in} frames: {exc}") from exc

    duration = frames_in / info.samplerate
    if duration < min_duration_s:
        raise UnsupportedAudioError(f"call is {duration:.2f}s, minimum is {min_duration_s:.2f}s")

    masks = [speech_mask(e.finish()) for e in energies]
    features = compute_features(masks, speakers_for(channels, channel_map), FRAME_S)
    return PreprocessResult(
        duration_s=round(duration, 3),
        source_sample_rate=info.samplerate,
        channels=channels,
        source_format=f"{info.format}/{info.subtype}",
        outputs=outputs,
        features=features,
    )
