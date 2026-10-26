"""Outbox relay: publishes committed outbox rows to RabbitMQ.

    LISTEN outbox  (instant wake-up on commit)  +  poll every second (safety net)
    SELECT ... FOR UPDATE SKIP LOCKED           (run as many relays as you like)
    publish with publisher confirms             (row is marked only after the broker acks)

If the relay dies between publishing and marking, the row is published again: consumers
are idempotent, so duplicates are harmless. Rows whose publish is rejected (broker down,
bounded queue full) back off exponentially and stay in Postgres, which becomes the buffer.
"""

import asyncio
import contextlib
import json
import logging
import signal
import time
from datetime import UTC, datetime
from typing import Any

import aio_pika
import asyncpg
from aio_pika.abc import AbstractChannel, AbstractExchange
from prometheus_client import start_http_server

from callintel.config import Settings, get_settings
from callintel.db.session import asyncpg_dsn
from callintel.logging import setup_logging
from callintel.messaging.broker import connect
from callintel.messaging.topology import EXCHANGE_PIPELINE, declare_topology
from callintel.metrics import OUTBOX_BACKLOG, OUTBOX_LAG, OUTBOX_PUBLISH_FAILURES, OUTBOX_PUBLISHED

log = logging.getLogger("callintel.relay")

_CLAIM = """
    SELECT id, message_id, exchange, routing_key, payload, headers, created_at
    FROM outbox
    WHERE published_at IS NULL AND (next_attempt_at IS NULL OR next_attempt_at <= now())
    ORDER BY id
    LIMIT $1
    FOR UPDATE SKIP LOCKED
"""
_MARK_PUBLISHED = "UPDATE outbox SET published_at = now() WHERE id = ANY($1::bigint[])"
_MARK_FAILED = """
    UPDATE outbox
    SET attempts = attempts + 1, last_error = $2,
        next_attempt_at = now() + make_interval(secs => least(60, power(2, attempts)))
    WHERE id = $1
"""
_BACKLOG = "SELECT count(*) FROM outbox WHERE published_at IS NULL"
_GC = "DELETE FROM outbox WHERE published_at < now() - make_interval(hours => $1)"


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")


class OutboxRelay:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._wake = asyncio.Event()
        self._exchanges: dict[str, AbstractExchange] = {}
        self._channel: AbstractChannel
        self._pool: asyncpg.Pool
        self._listener: asyncpg.Connection | None = None

    async def run(self, stop: asyncio.Event) -> None:
        dsn = asyncpg_dsn(self.settings.database_url)
        self._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, init=_init_connection)
        connection = await connect(self.settings, "outbox-relay")
        try:
            self._channel = await connection.channel(publisher_confirms=True,
                                                     on_return_raises=True)
            await declare_topology(self._channel, self.settings)
            await self._listen(dsn)
            log.info("relay started")
            last_gc = last_backlog = 0.0
            while not stop.is_set():
                self._wake.clear()
                try:
                    published = await self._drain_batch()
                except Exception:
                    log.exception("outbox drain failed")
                    published = 0
                    await asyncio.sleep(1)
                now = time.monotonic()
                if now - last_backlog > 5:
                    last_backlog = now
                    await self._housekeeping(gc=now - last_gc > 300)
                    if now - last_gc > 300:
                        last_gc = now
                if published >= self.settings.relay_batch_size:
                    continue  # more rows are probably waiting
                await self._wait(stop)
        finally:
            if self._listener is not None:
                with contextlib.suppress(Exception):
                    await self._listener.close()
            await connection.close()
            await self._pool.close()

    async def _listen(self, dsn: str) -> None:
        try:
            self._listener = await asyncpg.connect(dsn)
            await self._listener.add_listener("outbox", lambda *_: self._wake.set())
        except Exception:
            self._listener = None
            log.warning("LISTEN unavailable; falling back to polling")

    async def _wait(self, stop: asyncio.Event) -> None:
        if self._listener is None or self._listener.is_closed():
            await self._listen(asyncpg_dsn(self.settings.database_url))
        waiters = [asyncio.create_task(self._wake.wait()), asyncio.create_task(stop.wait())]
        try:
            await asyncio.wait(waiters, timeout=self.settings.relay_poll_interval_s,
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()

    async def _drain_batch(self) -> int:
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(_CLAIM, self.settings.relay_batch_size)
            if not rows:
                return 0
            results = await asyncio.gather(*(self._publish(r) for r in rows),
                                           return_exceptions=True)
            ok = [r["id"] for r, res in zip(rows, results, strict=True)
                  if not isinstance(res, BaseException)]
            if ok:
                await conn.execute(_MARK_PUBLISHED, ok)
            for row, res in zip(rows, results, strict=True):
                if isinstance(res, BaseException):
                    OUTBOX_PUBLISH_FAILURES.labels(row["exchange"]).inc()
                    await conn.execute(_MARK_FAILED, row["id"], repr(res)[:500])
            if len(ok) < len(rows):
                log.warning("some outbox rows were not published",
                            extra={"published": len(ok), "failed": len(rows) - len(ok)})
            return len(ok)

    async def _publish(self, row: Any) -> None:
        exchange = self._exchanges.get(row["exchange"])
        if exchange is None:
            exchange = await self._channel.get_exchange(row["exchange"])
            self._exchanges[row["exchange"]] = exchange
        message = aio_pika.Message(
            body=json.dumps(row["payload"], ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(row["message_id"]),
            timestamp=datetime.now(UTC),
            headers=row["headers"] or {},
        )
        # Pipeline messages must land in a queue; events may legitimately have no listener.
        await exchange.publish(message, routing_key=row["routing_key"],
                               mandatory=row["exchange"] == EXCHANGE_PIPELINE, timeout=10)
        OUTBOX_PUBLISHED.labels(row["exchange"]).inc()
        OUTBOX_LAG.observe((datetime.now(UTC) - row["created_at"]).total_seconds())

    async def _housekeeping(self, *, gc: bool) -> None:
        try:
            async with self._pool.acquire() as conn:
                OUTBOX_BACKLOG.set(await conn.fetchval(_BACKLOG))
                if gc:
                    deleted = await conn.execute(_GC, self.settings.outbox_retention_hours)
                    log.info("outbox gc", extra={"result": deleted})
        except Exception:
            log.warning("outbox housekeeping failed", exc_info=True)


async def _main() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    start_http_server(settings.metrics_port)
    await OutboxRelay(settings).run(stop)


def main() -> None:
    setup_logging(get_settings().log_level)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
