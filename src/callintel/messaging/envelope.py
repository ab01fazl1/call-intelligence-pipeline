"""Wire format for everything that travels over RabbitMQ."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Stage(StrEnum):
    PREPROCESS = "preprocess"
    ASR = "asr"
    ANALYSIS = "analysis"


NEXT_STAGE: dict[Stage, Stage | None] = {
    Stage.PREPROCESS: Stage.ASR,
    Stage.ASR: Stage.ANALYSIS,
    Stage.ANALYSIS: None,
}


def _now() -> datetime:
    return datetime.now(UTC)


class StageMessage(BaseModel):
    """A request to run one pipeline stage for one call.

    Deliberately small: it carries identifiers, never audio. Unknown fields are ignored so
    that producers can roll out new fields before consumers (tolerant reader).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = 1
    message_id: UUID = Field(default_factory=uuid4)
    call_id: UUID
    stage: Stage
    attempt: int = Field(default=1, ge=1)
    trace_id: str
    origin: Literal["pipeline", "reprocess", "replay"] = "pipeline"
    force: bool = False  # re-run even if this stage/version already succeeded
    created_at: datetime = Field(default_factory=_now)

    def next(self, stage: Stage) -> "StageMessage":
        return self.model_copy(
            update={"stage": stage, "message_id": uuid4(), "attempt": 1, "created_at": _now()}
        )

    def retry(self) -> "StageMessage":
        return self.model_copy(update={"attempt": self.attempt + 1, "created_at": _now()})


class CallEvent(BaseModel):
    """Fire-and-forget notification for dashboards / webhooks. Never drives processing."""

    event_id: UUID = Field(default_factory=uuid4)
    type: str  # received | stage.started | stage.completed | stage.retrying | completed | failed
    call_id: UUID
    status: str
    stage: Stage | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=_now)

    @property
    def routing_key(self) -> str:
        return f"call.{self.type}"
