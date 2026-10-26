"""Live call events for the dashboard: RabbitMQ topic -> in-process fan-out -> SSE."""

import asyncio
import logging

from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection

from callintel.messaging.topology import EXCHANGE_EVENTS

log = logging.getLogger(__name__)


class Broadcaster:
    """Bounded per-client queues: a slow browser loses old events instead of growing memory."""

    def __init__(self, buffer: int = 256) -> None:
        self._buffer = buffer
        self._subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._buffer)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    def publish(self, data: str) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(data)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)


async def consume_events(connection: AbstractRobustConnection, broadcaster: Broadcaster) -> None:
    channel = await connection.channel()
    exchange = await channel.get_exchange(EXCHANGE_EVENTS)
    # One exclusive, auto-deleted queue per gateway replica: every replica sees every event.
    queue = await channel.declare_queue("", exclusive=True, auto_delete=True,
                                        arguments={"x-max-length": 10_000})
    await queue.bind(exchange, routing_key="call.#")

    async def on_message(message: AbstractIncomingMessage) -> None:
        broadcaster.publish(message.body.decode())

    await queue.consume(on_message, no_ack=True)  # best effort by design
    log.info("event fan-out ready")
