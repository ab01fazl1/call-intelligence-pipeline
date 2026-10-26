"""RabbitMQ topology. Declared idempotently by every process on startup.

    callintel.pipeline (direct) --rk=<stage>--> callintel.<stage>            [quorum]
    callintel.retry    (direct) --rk=<stage>.<delay>--> callintel.<stage>.retry.<delay>s
                                     (TTL, then dead-letters back to callintel.pipeline/<stage>)
    callintel.dlx      (fanout) --> callintel.parking-lot   (kept for humans / replay)
                                --> callintel.dead-letters  (consumed: marks the call failed)
    callintel.events   (topic)  --rk=call.*--> one exclusive queue per gateway replica
"""

from aio_pika import ExchangeType
from aio_pika.abc import AbstractChannel

from callintel.config import Settings
from callintel.messaging.envelope import Stage

EXCHANGE_PIPELINE = "callintel.pipeline"
EXCHANGE_RETRY = "callintel.retry"
EXCHANGE_DLX = "callintel.dlx"
EXCHANGE_EVENTS = "callintel.events"
PARKING_QUEUE = "callintel.parking-lot"
DEAD_LETTER_QUEUE = "callintel.dead-letters"


def stage_queue(stage: Stage) -> str:
    return f"callintel.{stage.value}"


def retry_queue(stage: Stage, delay_s: int) -> str:
    return f"callintel.{stage.value}.retry.{delay_s}s"


def retry_routing_key(stage: Stage, delay_s: int) -> str:
    return f"{stage.value}.{delay_s}"


async def declare_topology(channel: AbstractChannel, settings: Settings) -> None:
    pipeline = await channel.declare_exchange(EXCHANGE_PIPELINE, ExchangeType.DIRECT, durable=True)
    retry = await channel.declare_exchange(EXCHANGE_RETRY, ExchangeType.DIRECT, durable=True)
    dlx = await channel.declare_exchange(EXCHANGE_DLX, ExchangeType.FANOUT, durable=True)
    await channel.declare_exchange(EXCHANGE_EVENTS, ExchangeType.TOPIC, durable=True)

    for stage in Stage:
        work = await channel.declare_queue(
            stage_queue(stage),
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                # Poison-message guard: a message whose processing kills the worker is
                # redelivered at most this many times before it is dead-lettered.
                "x-delivery-limit": settings.delivery_limit,
                "x-dead-letter-exchange": EXCHANGE_DLX,
                # Never lose a dead-lettered message, even if the DLX targets are unavailable.
                "x-dead-letter-strategy": "at-least-once",
                # Bounded queue: publishers get a nack instead of the broker eating all memory.
                "x-max-length": settings.queue_max_length,
                "x-overflow": "reject-publish",
            },
        )
        await work.bind(pipeline, routing_key=stage.value)

        for delay in settings.retry_delays_s:
            parked = await channel.declare_queue(
                retry_queue(stage, delay),
                durable=True,
                arguments={
                    "x-queue-type": "quorum",
                    "x-message-ttl": delay * 1000,
                    "x-dead-letter-exchange": EXCHANGE_PIPELINE,
                    "x-dead-letter-routing-key": stage.value,
                    # If the work queue is full when the TTL fires, keep the message here
                    # instead of dropping it.
                    "x-dead-letter-strategy": "at-least-once",
                    "x-overflow": "reject-publish",
                },
            )
            await parked.bind(retry, routing_key=retry_routing_key(stage, delay))

    parking = await channel.declare_queue(
        PARKING_QUEUE, durable=True, arguments={"x-queue-type": "quorum"}
    )
    await parking.bind(dlx)
    # Classic queue on purpose: quorum queues enforce a delivery limit, and this consumer
    # must be able to retry forever while the database is down.
    handler = await channel.declare_queue(DEAD_LETTER_QUEUE, durable=True)
    await handler.bind(dlx)
