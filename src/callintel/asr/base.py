from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class ASRRequest:
    call_id: UUID
    sha256: str  # of the original upload; lets the mock find its ground truth
    channel: int
    channels: int
    duration_s: float


class ASRBackend(Protocol):
    @property
    def name(self) -> str:
        """Model identity, stored with every transcript (e.g. "faster-whisper/small-int8")."""
        ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def transcribe(self, audio: Path, request: ASRRequest) -> list[Segment]:
        """Transcribe one 16 kHz mono channel file."""
        ...
