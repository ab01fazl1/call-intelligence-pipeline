"""Consumes the dead-letter fan-out and records the failure on the call.

Messages reach the DLX two ways, and both end up here:
  * a worker parked them explicitly (permanent error / retries exhausted), with
    x-callintel-* headers describing the failure;
  * RabbitMQ dead-lettered them because the quorum queue's delivery limit was exceeded,
    i.e. a worker kept dying while holding the message (x-first-death-reason=delivery_limit).
A copy of each message stays in the parking-lot queue for inspection and replay.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from aio_pika.abc import AbstractIncomingMessage
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callintel.config import Settings
from callintel.db.models import Call, CallStatus, StageRun, StageStatus
from callintel.db.outbox import enqueue_event
from callintel.logging import log_context
from callintel.messaging.broker import connect, header_str
from callintel.messaging.envelope import CallEvent, StageMessage
from callintel.messaging.topology import DEAD_LETTER_QUEUE, declare_topology
from callintel.metrics import PARKED

log = logging.getLogger(__name__)


class DeadLetterHandler:
    def __init__(self, settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.settings = settings
        self.sessions = sessions
        self._tasks: set[asyncio.Task[None]] = set()

    async def run(self, stop: asyncio.Event) -> None:
        connection = await connect(self.settings, "dead-letter-handler")
        try:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=10)
            await declare_topology(channel, self.settings)
            queue = await channel.get_queue(DEAD_LETTER_QUEUE)
            tag = await queue.consume(self._on_message)
            log.info("consuming", extra={"queue": DEAD_LETTER_QUEUE})
            await stop.wait()
            await queue.cancel(tag)
            if self._tasks:
                await asyncio.wait(self._tasks, timeout=10)
        finally:
            await connection.close()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        task = asyncio.create_task(self._handle(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, message: AbstractIncomingMessage) -> None:
        headers = message.headers or {}
        reason = (header_str(headers, "x-callintel-reason")
                  or header_str(headers, "x-first-death-reason") or "unknown")
        error = header_str(headers, "x-callintel-error") or (
            "delivery limit exceeded: workers repeatedly died while processing this message"
            if reason == "delivery_limit" else reason
        )
        try:
            env = StageMessage.model_validate_json(message.body)
        except ValidationError:
            log.error("unparseable dead letter; left in parking lot", extra={"reason": reason})
            PARKED.labels("unknown", reason).inc()
            await message.ack()
            return

        with log_context(call_id=str(env.call_id), stage=env.stage.value, trace_id=env.trace_id):
            try:
                await self._record(env, reason, error)
            except Exception:
                log.exception("could not record dead letter; will retry")
                await asyncio.sleep(5)
                with contextlib.suppress(Exception):
                    await message.nack(requeue=True)
                return
            PARKED.labels(env.stage.value, reason).inc()
            log.error("call failed", extra={"reason": reason, "error": error})
            await message.ack()

    async def _record(self, env: StageMessage, reason: str, error: str) -> None:
        async with self.sessions() as s, s.begin():
            call = await s.get(Call, env.call_id, with_for_update=True)
            if call is None:
                return
            succeeded = await s.scalar(
                select(StageRun.id).where(StageRun.call_id == call.id,
                                          StageRun.stage == env.stage.value,
                                          StageRun.status == StageStatus.SUCCEEDED).limit(1)
            )
            if succeeded is not None and env.origin == "pipeline":
                log.info("stale dead letter: stage already succeeded via another delivery")
                return
            await s.execute(
                update(StageRun)
                .where(StageRun.call_id == call.id, StageRun.stage == env.stage.value,
                       StageRun.status != StageStatus.SUCCEEDED)
                .values(status=StageStatus.FAILED, last_error=error[:2000],
                        finished_at=datetime.now(UTC))
            )
            if env.origin != "pipeline" and call.completed_at is not None:
                # A failed re-run must not hide the results the call already has.
                call.status = CallStatus.COMPLETED
                enqueue_event(s, CallEvent(type="reprocess.failed", call_id=call.id,
                                           status=call.status, stage=env.stage,
                                           data={"reason": reason, "error": error}))
                return
            call.status = CallStatus.FAILED
            call.error = f"{env.stage.value}: {error}"[:2000]
            enqueue_event(s, CallEvent(type="failed", call_id=call.id, status=CallStatus.FAILED,
                                       stage=env.stage, data={"reason": reason, "error": error}))
