"""Transactional outbox: the only way pipeline code publishes to RabbitMQ.

Rows are inserted in the same transaction as the state change they announce; the relay
(callintel.relay) publishes them afterwards. A crash can therefore duplicate a message but
never lose one, and consumers are idempotent.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from callintel.db.models import OutboxMessage
from callintel.messaging.envelope import CallEvent, StageMessage
from callintel.messaging.topology import EXCHANGE_EVENTS, EXCHANGE_PIPELINE


def enqueue_stage(session: AsyncSession, message: StageMessage) -> None:
    session.add(
        OutboxMessage(
            message_id=message.message_id,
            exchange=EXCHANGE_PIPELINE,
            routing_key=message.stage.value,
            payload=message.model_dump(mode="json"),
            headers={"x-trace-id": message.trace_id},
        )
    )


def enqueue_event(session: AsyncSession, event: CallEvent) -> None:
    session.add(
        OutboxMessage(
            message_id=event.event_id,
            exchange=EXCHANGE_EVENTS,
            routing_key=event.routing_key,
            payload=event.model_dump(mode="json"),
            headers={},
        )
    )
