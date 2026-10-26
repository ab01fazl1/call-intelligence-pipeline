from callintel.asr.base import ASRBackend
from callintel.config import Settings


def make_backend(settings: Settings) -> ASRBackend:
    if settings.asr_backend == "whisper":
        from callintel.asr.whisper import WhisperASR  # optional dependency

        return WhisperASR(settings)
    from callintel.asr.mock import MockASR

    return MockASR(settings)
