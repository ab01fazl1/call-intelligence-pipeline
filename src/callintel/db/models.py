import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB}


class CallStatus(StrEnum):
    RECEIVED = "received"
    PREPROCESSING = "preprocessing"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class StageStatus(StrEnum):
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _ts(**kw: Any) -> Any:
    return mapped_column(DateTime(timezone=True), **kw)


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    object_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), index=True)
    trace_id: Mapped[str] = mapped_column(String(32))
    duration_s: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    audio_features: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    updated_at: Mapped[datetime] = _ts(server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = _ts()


class StageRun(Base):
    """One row per (call, stage, version). The unit of idempotency for workers."""

    __tablename__ = "stage_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = _ts()
    finished_at: Mapped[datetime | None] = _ts()


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(8), default="fa")
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    full_text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)  # Persian-normalized, trigram indexed
    processing_s: Mapped[float] = mapped_column(Float)
    real_time_factor: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _ts(server_default=func.now())


class QAResult(Base):
    """Versioned analysis output, unique per (call, analyzer version, transcript model):
    a new analyzer or a new transcript adds rows, never overwrites."""

    __tablename__ = "qa_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    analyzer_version: Mapped[str] = mapped_column(String(32))
    transcript_model: Mapped[str] = mapped_column(String(128))
    score: Mapped[float] = mapped_column(Float)
    compliance: Mapped[str] = mapped_column(String(16))
    flags: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    details: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = _ts(server_default=func.now())


class OutboxMessage(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    exchange: Mapped[str] = mapped_column(String(128))
    routing_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]]
    headers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts(server_default=func.now())
    next_attempt_at: Mapped[datetime | None] = _ts()
    published_at: Mapped[datetime | None] = _ts()
