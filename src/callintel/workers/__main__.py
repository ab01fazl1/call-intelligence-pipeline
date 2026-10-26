"""python -m callintel.workers {preprocess|asr|analysis|deadletter}"""

import argparse
import asyncio
import logging
import signal

from prometheus_client import start_http_server

from callintel.config import get_settings
from callintel.db.session import make_engine, make_sessionmaker
from callintel.logging import setup_logging
from callintel.storage import ObjectStore
from callintel.workers.base import Deps, StageWorker
from callintel.workers.deadletter import DeadLetterHandler
from callintel.workers.stages import HANDLERS

log = logging.getLogger("callintel.workers")


async def _main(kind: str) -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    start_http_server(settings.metrics_port)
    engine = make_engine(settings.database_url, pool_size=settings.worker_concurrency + 2)
    sessions = make_sessionmaker(engine)
    store = ObjectStore(settings)
    await store.start()
    try:
        if kind == "deadletter":
            await DeadLetterHandler(settings, sessions).run(stop)
            return
        handler = HANDLERS[kind](Deps(settings, sessions, store))
        await handler.start()
        try:
            await StageWorker(handler, Deps(settings, sessions, store)).run(stop)
        finally:
            await handler.close()
    finally:
        await store.close()
        await engine.dispose()
        log.info("stopped", extra={"worker": kind})


def main() -> None:
    parser = argparse.ArgumentParser(prog="callintel.workers")
    parser.add_argument("kind", choices=[*HANDLERS, "deadletter"])
    args = parser.parse_args()
    setup_logging(get_settings().log_level)
    asyncio.run(_main(args.kind))


if __name__ == "__main__":
    main()
