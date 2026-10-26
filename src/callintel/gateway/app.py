from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from callintel import __version__
from callintel.config import get_settings
from callintel.db.session import make_engine, make_sessionmaker
from callintel.gateway.api import AppState, BacklogGuard, router
from callintel.gateway.events import Broadcaster, consume_events
from callintel.logging import setup_logging
from callintel.messaging.broker import connect
from callintel.messaging.topology import declare_topology
from callintel.storage import ObjectStore

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    store = ObjectStore(settings)
    await store.start()
    await store.ensure_bucket()
    amqp = await connect(settings, "gateway")
    channel = await amqp.channel()
    await declare_topology(channel, settings)
    await channel.close()
    broadcaster = Broadcaster()
    await consume_events(amqp, broadcaster)
    app.state.ctx = AppState(settings, sessions, store, amqp, broadcaster,
                             BacklogGuard(sessions, settings.outbox_backlog_limit))
    try:
        yield
    finally:
        await amqp.close()
        await store.close()
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Call Intelligence Pipeline",
        version=__version__,
        description="Streaming ingestion, Persian ASR and QA/compliance scoring for "
                    "call-center recordings.",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app


app = create_app()
