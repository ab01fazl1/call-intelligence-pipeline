"""Generic stage worker.

Per message:  claim (short txn)  ->  run (no txn held; may take minutes)
              ->  complete (short txn: compare-and-set + results + next stage via outbox)
              ->  ack

Delivery is at-least-once; idempotency comes from the stage_runs row for
(call, stage, version): a stage that already succeeded is acknowledged and skipped, and the
completion compare-and-set guarantees that two racing deliveries commit results only once.
"""

import asyncio
import contextlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractIncomingMessage
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callintel.config import Settings
from callintel.db.models import Call, CallStatus, StageRun, StageStatus
from callintel.db.outbox import enqueue_event, enqueue_stage
from callintel.errors import CallNotFound
from callintel.logging import log_context
from callintel.messaging.broker import connect, to_amqp
from callintel.messaging.envelope import NEXT_STAGE, CallEvent, Stage, StageMessage
from callintel.messaging.topology import (
    EXCHANGE_DLX,
    EXCHANGE_RETRY,
    declare_topology,
    retry_routing_key,
    stage_queue,
)
from callintel.metrics import CALL_E2E, STAGE_INFLIGHT, STAGE_MESSAGES, STAGE_SECONDS
from callintel.storage import ObjectStore
from callintel.workers.policy import Retry, decide

log = logging.getLogger(__name__)

STAGE_STATUS = {
    Stage.PREPROCESS: CallStatus.PREPROCESSING,
    Stage.ASR: CallStatus.TRANSCRIBING,
    Stage.ANALYSIS: CallStatus.ANALYZING,
}


@dataclass(frozen=True)
class CallSnapshot:
    """Immutable copy of the call row handed to handlers outside any transaction."""

    id: UUID
    sha256: str
    object_key: str
    content_type: str
    trace_id: str
    channels: int | None
    duration_s: float | None
    audio_features: dict[str, Any] | None
    created_at: datetime

    @classmethod
    def of(cls, call: Call) -> "CallSnapshot":
        return cls(call.id, call.sha256, call.object_key, call.content_type, call.trace_id,
                   call.channels, call.duration_s, call.audio_features, call.created_at)


@dataclass(frozen=True)
class Deps:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    store: ObjectStore


class StageHandler[R](ABC):
    stage: ClassVar[Stage]
    # True when a committed result changes the next stage's input, so the next stage must
    # run again even if it already succeeded for its version (e.g. a new ASR model).
    invalidates_downstream: ClassVar[bool] = False

    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.settings = deps.settings

    @property
    @abstractmethod
    def version(self) -> str:
        """Identity of the code/model producing results; part of the idempotency key."""

    @property
    def concurrency(self) -> int:
        return self.settings.worker_concurrency

    async def start(self) -> None:  # noqa: B027 - optional hook
        pass

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    def timeout_s(self, call: CallSnapshot) -> float:
        return 600.0

    @abstractmethod
    async def run(self, call: CallSnapshot) -> R:
        """Do the work. Must not hold a database transaction open (it may read)."""

    @abstractmethod
    async def persist(self, session: AsyncSession, call: Call, result: R) -> CallStatus:
        """Write results inside the completion transaction; return the call's new status."""

    def summary(self, result: R) -> dict[str, Any]:
        return {}


def _describe(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timed out"
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class StageWorker:
    def __init__(self, handler: StageHandler[Any], deps: Deps) -> None:
        self.handler = handler
        self.stage = handler.stage
        self.settings = deps.settings
        self.sessions = deps.sessions
        self._tasks: set[asyncio.Task[None]] = set()
        self._retry: AbstractExchange
        self._dlx: AbstractExchange

    async def run(self, stop: asyncio.Event) -> None:
        connection = await connect(self.settings, f"worker-{self.stage.value}")
        try:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            await channel.set_qos(prefetch_count=self.handler.concurrency)
            await declare_topology(channel, self.settings)
            self._retry = await channel.get_exchange(EXCHANGE_RETRY)
            self._dlx = await channel.get_exchange(EXCHANGE_DLX)
            queue = await channel.get_queue(stage_queue(self.stage))
            tag = await queue.consume(self._on_message)
            log.info("consuming", extra={"queue": queue.name, "version": self.handler.version,
                                         "prefetch": self.handler.concurrency})
            await stop.wait()

            # Graceful shutdown: stop taking work, let in-flight messages finish. Anything
            # still running after the grace period is cancelled and left unacked, so
            # RabbitMQ redelivers it to another worker.
            log.info("draining", extra={"inflight": len(self._tasks)})
            await queue.cancel(tag)
            if self._tasks:
                _, pending = await asyncio.wait(self._tasks, timeout=self.settings.shutdown_grace_s)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=5)
            log.info("drained")
        finally:
            await connection.close()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        task = asyncio.create_task(self._handle(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, message: AbstractIncomingMessage) -> None:
        stage = self.stage.value
        STAGE_INFLIGHT.labels(stage).inc()
        started = time.perf_counter()
        try:
            try:
                env = StageMessage.model_validate_json(message.body)
            except ValidationError as exc:
                log.error("malformed message", extra={"error": str(exc)[:500]})
                await self._park_raw(message, exc)
                STAGE_MESSAGES.labels(stage, "malformed").inc()
                return
            with log_context(call_id=str(env.call_id), stage=stage, trace_id=env.trace_id,
                             attempt=env.attempt, origin=env.origin,
                             redelivered=message.redelivered):
                outcome = await self._process(env, message)
                STAGE_MESSAGES.labels(stage, outcome).inc()
                if outcome == "succeeded":
                    STAGE_SECONDS.labels(stage).observe(time.perf_counter() - started)
                log.info("message done", extra={"outcome": outcome,
                                                "seconds": round(time.perf_counter() - started, 3)})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("unhandled error in message handler")
            with contextlib.suppress(Exception):
                await message.nack(requeue=True)
        finally:
            STAGE_INFLIGHT.labels(stage).dec()

    async def _process(self, env: StageMessage, message: AbstractIncomingMessage) -> str:
        try:
            claim = await self._claim(env)
        except Exception as exc:  # call vanished (permanent) or database unavailable
            return await self._fail(env, message, exc)
        if claim is None:
            await message.ack()
            return "duplicate"

        try:
            result = await asyncio.wait_for(self.handler.run(claim),
                                            timeout=self.handler.timeout_s(claim))
            committed = await self._complete(env, claim, result)
        except Exception as exc:  # CancelledError (shutdown) is a BaseException: not caught
            return await self._fail(env, message, exc)
        await message.ack()
        return "succeeded" if committed else "duplicate"

    async def _claim(self, env: StageMessage) -> CallSnapshot | None:
        async with self.sessions() as s, s.begin():
            call = await s.get(Call, env.call_id, with_for_update=True)
            if call is None:
                raise CallNotFound(str(env.call_id))
            run = await s.scalar(
                select(StageRun).where(
                    StageRun.call_id == call.id,
                    StageRun.stage == self.stage.value,
                    StageRun.version == self.handler.version,
                ).with_for_update()
            )
            if run is not None and run.status == StageStatus.SUCCEEDED and not env.force:
                return None
            if run is None:
                run = StageRun(call_id=call.id, stage=self.stage.value,
                               version=self.handler.version, attempts=0)
                s.add(run)
            run.status = StageStatus.RUNNING
            run.attempts += 1
            run.started_at = datetime.now(UTC)
            run.finished_at = None
            call.status = STAGE_STATUS[self.stage]
            enqueue_event(s, CallEvent(
                type="stage.started", call_id=call.id, status=call.status, stage=self.stage,
                data={"attempt": run.attempts, "version": self.handler.version,
                      "origin": env.origin},
            ))
            return CallSnapshot.of(call)

    async def _complete(self, env: StageMessage, claim: CallSnapshot, result: Any) -> bool:
        now = datetime.now(UTC)
        e2e: float | None = None
        async with self.sessions() as s, s.begin():
            # Lock order (call, then stage_run) matches _claim to rule out deadlocks.
            call = await s.get(Call, claim.id, with_for_update=True)
            if call is None:
                raise CallNotFound(str(claim.id))
            row = (await s.execute(
                update(StageRun)
                .where(
                    StageRun.call_id == claim.id,
                    StageRun.stage == self.stage.value,
                    StageRun.version == self.handler.version,
                    StageRun.status != StageStatus.SUCCEEDED,
                )
                .values(status=StageStatus.SUCCEEDED, finished_at=now, last_error=None)
                .returning(StageRun.attempts)
            )).first()
            if row is None:
                log.info("another delivery already completed this stage; discarding result")
                return False

            status = await self.handler.persist(s, call, result)
            call.status = status
            call.error = None
            summary = self.handler.summary(result)
            if (nxt := NEXT_STAGE[self.stage]) is not None:
                follow = env.next(nxt)
                if self.handler.invalidates_downstream:
                    # Enqueued once per *committed* result (the CAS above), so forcing
                    # cannot amplify duplicates; a relay re-publish re-runs an idempotent upsert.
                    follow = follow.model_copy(update={"force": True})
                enqueue_stage(s, follow)
            enqueue_event(s, CallEvent(
                type="stage.completed", call_id=call.id, status=status, stage=self.stage,
                data={"attempts": row.attempts, "version": self.handler.version, **summary},
            ))
            if status == CallStatus.COMPLETED:
                if call.completed_at is None:
                    call.completed_at = now
                    if env.origin == "pipeline":
                        e2e = (now - call.created_at).total_seconds()
                enqueue_event(s, CallEvent(type="completed", call_id=call.id, status=status,
                                           data=summary))
        if e2e is not None:
            CALL_E2E.observe(e2e)
        return True

    async def _fail(self, env: StageMessage, message: AbstractIncomingMessage,
                    exc: BaseException) -> str:
        decision = decide(env.attempt, exc, self.settings.retry_delays_s)
        error = _describe(exc)
        try:
            if isinstance(decision, Retry):
                log.warning("stage failed; scheduling retry",
                            extra={"error": error, "delay_s": decision.delay_s})
                await self._retry.publish(
                    to_amqp(env.retry(), message_id=str(env.message_id),
                            headers={"x-trace-id": env.trace_id}),
                    routing_key=retry_routing_key(self.stage, decision.delay_s),
                    mandatory=True,
                )
                await self._mark_retrying(env, error)
                outcome = "retried"
            else:
                log.error("stage failed; parking message",
                          extra={"error": error, "reason": decision.reason})
                await self._dlx.publish(
                    to_amqp(env, message_id=str(env.message_id), headers={
                        "x-trace-id": env.trace_id,
                        "x-callintel-reason": decision.reason,
                        "x-callintel-error": error[:1000],
                        "x-callintel-error-type": type(exc).__name__,
                        "x-callintel-version": self.handler.version,
                    }),
                    routing_key=self.stage.value,
                    mandatory=True,
                )
                outcome = "parked"
            await message.ack()
            return outcome
        except Exception:
            # Broker trouble while scheduling the retry: hand the message back instead.
            log.exception("could not schedule retry/park; requeueing")
            await asyncio.sleep(1)
            with contextlib.suppress(Exception):
                await message.nack(requeue=True)
            return "requeued"

    async def _mark_retrying(self, env: StageMessage, error: str) -> None:
        try:
            async with self.sessions() as s, s.begin():
                await s.execute(
                    update(StageRun)
                    .where(StageRun.call_id == env.call_id, StageRun.stage == self.stage.value,
                           StageRun.version == self.handler.version,
                           StageRun.status != StageStatus.SUCCEEDED)
                    .values(status=StageStatus.RETRYING, last_error=error[:2000])
                )
                enqueue_event(s, CallEvent(
                    type="stage.retrying", call_id=env.call_id, status=STAGE_STATUS[self.stage],
                    stage=self.stage, data={"attempt": env.attempt, "error": error[:300]},
                ))
        except Exception:
            log.warning("could not record retry state (database unavailable?)")

    async def _park_raw(self, message: AbstractIncomingMessage, exc: Exception) -> None:
        try:
            await self._dlx.publish(
                aio_pika.Message(body=message.body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                 headers={"x-callintel-reason": "malformed",
                                          "x-callintel-error": str(exc)[:1000]}),
                routing_key=self.stage.value,
                mandatory=True,
            )
            await message.ack()
        except Exception:
            with contextlib.suppress(Exception):
                await message.nack(requeue=True)
