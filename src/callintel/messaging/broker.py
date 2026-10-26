from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika.abc import AbstractRobustConnection
from pydantic import BaseModel

from callintel.config import Settings


async def connect(settings: Settings, name: str) -> AbstractRobustConnection:
    """Robust connection: reconnects and restores channels, queues and consumers."""
    return await aio_pika.connect_robust(
        settings.amqp_url, client_properties={"connection_name": f"{settings.service_name}:{name}"}
    )


def to_amqp(model: BaseModel, *, message_id: str, headers: dict[str, Any] | None = None,
            ) -> aio_pika.Message:
    return aio_pika.Message(
        body=model.model_dump_json().encode(),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=message_id,
        timestamp=datetime.now(UTC),
        headers=headers or {},
    )


def header_str(headers: dict[str, Any] | None, key: str) -> str | None:
    value = (headers or {}).get(key)
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)
