from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from callintel.db.models import CallStatus
from callintel.messaging.envelope import Stage


class CallAccepted(BaseModel):
    id: UUID
    status: str
    duplicate: bool
    sha256: str
    size_bytes: int
    links: dict[str, str]


class StageRunOut(BaseModel):
    stage: str
    version: str
    status: str
    attempts: int
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class QAResultOut(BaseModel):
    analyzer_version: str
    transcript_model: str
    score: float
    compliance: str
    flags: list[str]
    details: dict[str, Any]
    created_at: datetime


class TranscriptOut(BaseModel):
    model: str
    language: str
    segments: list[dict[str, Any]]
    processing_s: float
    real_time_factor: float | None
    created_at: datetime


class CallSummary(BaseModel):
    id: UUID
    external_id: str | None
    status: str
    duration_s: float | None
    channels: int | None
    created_at: datetime
    completed_at: datetime | None
    error: str | None
    analyzer_version: str | None = None
    score: float | None = None
    compliance: str | None = None
    flags: list[str] = Field(default_factory=list)


class CallDetail(CallSummary):
    sha256: str
    size_bytes: int
    content_type: str
    trace_id: str
    attributes: dict[str, Any]
    audio_features: dict[str, Any] | None
    stages: list[StageRunOut]
    transcript: TranscriptOut | None  # latest
    transcripts: list[TranscriptOut]  # every model that transcribed this call
    qa_results: list[QAResultOut]


class SearchHit(BaseModel):
    call_id: UUID
    model: str
    matches: list[dict[str, Any]]


class ReprocessRequest(BaseModel):
    stage: Stage = Stage.ANALYSIS
    call_ids: list[UUID] | None = None
    status: CallStatus | None = CallStatus.COMPLETED
    force: bool = False
    limit: int = Field(default=1000, ge=1, le=10_000)


class ReprocessResponse(BaseModel):
    stage: Stage
    enqueued: int


class ReplayResponse(BaseModel):
    replayed: int
    skipped: int
    discarded: int
    call_ids: list[UUID]
