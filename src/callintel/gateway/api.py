import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID, uuid4

import aio_pika
from aio_pika.abc import AbstractRobustConnection
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import case, exists, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect

from callintel.config import Settings
from callintel.db.models import Call, CallStatus, OutboxMessage, QAResult, StageRun, Transcript
from callintel.db.outbox import enqueue_event, enqueue_stage
from callintel.gateway import schemas as sch
from callintel.gateway.events import Broadcaster
from callintel.gateway.upload import UploadBody, UploadRejected
from callintel.messaging.broker import header_str, to_amqp
from callintel.messaging.envelope import CallEvent, Stage, StageMessage
from callintel.messaging.topology import EXCHANGE_PIPELINE, PARKING_QUEUE
from callintel.metrics import OUTBOX_BACKLOG, UPLOAD_BYTES, UPLOAD_SECONDS, UPLOADS
from callintel.storage import ObjectStore, raw_key
from callintel.text.normalize import normalize

log = logging.getLogger(__name__)
router = APIRouter()


class BacklogGuard:
    """End-to-end backpressure. Bounded queues reject publishes when full, so unpublished
    rows pile up in the outbox; past a threshold the gateway sheds load with 503."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], limit: int,
                 ttl_s: float = 1.0) -> None:
        self._sessions, self._limit, self._ttl = sessions, limit, ttl_s
        self._value, self._at = 0, 0.0

    async def backlog(self) -> int:
        if time.monotonic() - self._at > self._ttl:
            async with self._sessions() as s:
                self._value = await s.scalar(
                    select(func.count()).select_from(OutboxMessage)
                    .where(OutboxMessage.published_at.is_(None))
                ) or 0
            self._at = time.monotonic()
            OUTBOX_BACKLOG.set(self._value)
        return self._value

    async def check(self) -> None:
        if await self.backlog() > self._limit:
            UPLOADS.labels("backpressure").inc()
            raise HTTPException(503, "pipeline backlog is full; retry later",
                                headers={"Retry-After": "30"})


@dataclass
class AppState:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    store: ObjectStore
    amqp: AbstractRobustConnection
    broadcaster: Broadcaster
    backlog: BacklogGuard


def _st(request: Request) -> AppState:
    return request.app.state.ctx


def _accepted(call: Call, *, duplicate: bool) -> sch.CallAccepted:
    return sch.CallAccepted(
        id=call.id, status=call.status, duplicate=duplicate, sha256=call.sha256,
        size_bytes=call.size_bytes,
        links={"self": f"/v1/calls/{call.id}", "events": "/v1/events"},
    )


# --------------------------------------------------------------------------- ingestion


@router.post(
    "/v1/calls", status_code=202, response_model=sch.CallAccepted, tags=["calls"],
    summary="Upload a call recording",
    description="Send the WAV/MP3 bytes as the raw request body (not multipart). The body is "
                "streamed to object storage in parts; it is never buffered whole in memory. "
                "Identical audio (same SHA-256) returns the existing call with 200.",
)
async def upload_call(
    request: Request,
    response: Response,
    external_id: Annotated[str | None, Query(max_length=128)] = None,
    agent_id: Annotated[str | None, Query(max_length=64)] = None,
) -> sch.CallAccepted:
    st = _st(request)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > st.settings.max_upload_bytes:
        UPLOADS.labels("too_large").inc()
        raise HTTPException(413, f"upload exceeds {st.settings.max_upload_bytes} bytes")
    await st.backlog.check()

    call_id = uuid4()
    key = raw_key(call_id)
    body = UploadBody(request.stream(), max_bytes=st.settings.max_upload_bytes)
    started = time.perf_counter()
    try:
        await st.store.upload_stream(
            key, body.chunks(),
            content_type=request.headers.get("content-type", "application/octet-stream"),
            part_size=st.settings.upload_part_bytes,
        )
    except UploadRejected as exc:
        UPLOADS.labels(type(exc).__name__).inc()
        raise HTTPException(exc.status_code, str(exc)) from None
    except ClientDisconnect:
        UPLOADS.labels("client_disconnect").inc()
        log.info("client disconnected mid-upload; multipart upload aborted")
        raise HTTPException(400, "client disconnected") from None
    except (BotoCoreError, ClientError):
        UPLOADS.labels("storage_error").inc()
        log.exception("object storage unavailable during upload")
        raise HTTPException(503, "storage temporarily unavailable",
                            headers={"Retry-After": "10"}) from None
    UPLOAD_SECONDS.observe(time.perf_counter() - started)
    UPLOAD_BYTES.inc(body.size)

    trace_id = uuid4().hex
    existing: Call | None = None
    try:
        try:
            async with st.sessions() as s, s.begin():
                existing = await s.scalar(select(Call).where(Call.sha256 == body.sha256))
                if existing is None:
                    s.add(Call(
                        id=call_id, external_id=external_id, sha256=body.sha256, object_key=key,
                        content_type=body.media_type, size_bytes=body.size,
                        status=CallStatus.RECEIVED, trace_id=trace_id,
                        attributes={"agent_id": agent_id} if agent_id else {},
                    ))
                    # Same transaction as the row: the job cannot be lost or orphaned.
                    enqueue_stage(s, StageMessage(call_id=call_id, stage=Stage.PREPROCESS,
                                                  trace_id=trace_id))
                    enqueue_event(s, CallEvent(type="received", call_id=call_id,
                                               status=CallStatus.RECEIVED,
                                               data={"external_id": external_id,
                                                     "size_bytes": body.size}))
        except IntegrityError:  # an identical upload committed first
            async with st.sessions() as s:
                existing = await s.scalar(select(Call).where(Call.sha256 == body.sha256))
    except Exception:
        log.exception("could not register upload; removing stored object")
        with contextlib.suppress(Exception):
            await st.store.delete(key)
        UPLOADS.labels("error").inc()
        raise HTTPException(503, "temporarily unable to accept uploads",
                            headers={"Retry-After": "10"}) from None

    if existing is not None:
        with contextlib.suppress(Exception):
            await st.store.delete(key)
        UPLOADS.labels("duplicate").inc()
        response.status_code = 200
        return _accepted(existing, duplicate=True)

    UPLOADS.labels("accepted").inc()
    response.headers["Location"] = f"/v1/calls/{call_id}"
    log.info("call accepted", extra={"call_id": str(call_id), "bytes": body.size,
                                     "trace_id": trace_id})
    return sch.CallAccepted(id=call_id, status=CallStatus.RECEIVED, duplicate=False,
                            sha256=body.sha256, size_bytes=body.size,
                            links={"self": f"/v1/calls/{call_id}", "events": "/v1/events"})


# --------------------------------------------------------------------------- queries


def _summary(call: Call, qa: QAResult | None) -> dict[str, Any]:
    return {
        "id": call.id, "external_id": call.external_id, "status": call.status,
        "duration_s": call.duration_s, "channels": call.channels, "created_at": call.created_at,
        "completed_at": call.completed_at, "error": call.error,
        "analyzer_version": qa.analyzer_version if qa else None,
        "score": qa.score if qa else None, "compliance": qa.compliance if qa else None,
        "flags": qa.flags if qa else [],
    }


def _pick_qa(results: list[QAResult], preferred: str) -> QAResult | None:
    """Newest result of the preferred analyzer version, else the newest of any version."""
    candidates = [r for r in results if r.analyzer_version == preferred] or results
    return max(candidates, key=lambda r: r.created_at, default=None)


@router.get("/v1/calls", response_model=list[sch.CallSummary], tags=["calls"])
async def list_calls(
    request: Request,
    status: CallStatus | None = None,
    flag: str | None = None,
    external_prefix: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
) -> list[dict[str, Any]]:
    st = _st(request)
    query = select(Call).order_by(Call.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Call.status == status)
    if external_prefix:
        query = query.where(Call.external_id.startswith(external_prefix, autoescape=True))
    if flag:
        query = query.where(exists().where(QAResult.call_id == Call.id,
                                           QAResult.flags.contains([flag])))
    async with st.sessions() as s:
        calls = list(await s.scalars(query))
        qa_rows = list(await s.scalars(
            select(QAResult).where(QAResult.call_id.in_([c.id for c in calls]))
        )) if calls else []
    by_call: dict[UUID, list[QAResult]] = {}
    for row in qa_rows:
        by_call.setdefault(row.call_id, []).append(row)
    preferred = st.settings.analyzer_version
    return [_summary(c, _pick_qa(by_call.get(c.id, []), preferred)) for c in calls]


@router.get("/v1/calls/{call_id}", response_model=sch.CallDetail, tags=["calls"])
async def get_call(request: Request, call_id: UUID) -> dict[str, Any]:
    st = _st(request)
    async with st.sessions() as s:
        call = await s.get(Call, call_id)
        if call is None:
            raise HTTPException(404, "call not found")
        stages = list(await s.scalars(
            select(StageRun).where(StageRun.call_id == call_id).order_by(StageRun.id)))
        transcripts = list(await s.scalars(
            select(Transcript).where(Transcript.call_id == call_id)
            .order_by(Transcript.created_at.desc())))
        transcript = transcripts[0] if transcripts else None
        qa = list(await s.scalars(
            select(QAResult).where(QAResult.call_id == call_id).order_by(QAResult.created_at)))
    return {
        **_summary(call, _pick_qa(qa, st.settings.analyzer_version)),
        "sha256": call.sha256, "size_bytes": call.size_bytes, "content_type": call.content_type,
        "trace_id": call.trace_id, "attributes": call.attributes,
        "audio_features": call.audio_features,
        "stages": [sch.StageRunOut.model_validate(r, from_attributes=True) for r in stages],
        "transcript": (sch.TranscriptOut.model_validate(transcript, from_attributes=True)
                       if transcript else None),
        "transcripts": [sch.TranscriptOut.model_validate(t, from_attributes=True)
                        for t in transcripts],
        "qa_results": [sch.QAResultOut.model_validate(r, from_attributes=True) for r in qa],
    }


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/v1/search", response_model=list[sch.SearchHit], tags=["calls"],
            summary="Search transcripts (Persian-normalized, trigram index)")
async def search(request: Request, q: Annotated[str, Query(min_length=2, max_length=200)],
                 limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict[str, Any]]:
    needle = normalize(q)
    if len(needle) < 2:
        raise HTTPException(422, "query too short after normalization")
    async with _st(request).sessions() as s:
        rows = list(await s.scalars(
            select(Transcript)
            .where(Transcript.search_text.ilike(f"%{_like_escape(needle)}%", escape="\\"))
            .order_by(Transcript.created_at.desc()).limit(limit)
        ))
    return [
        {"call_id": t.call_id, "model": t.model,
         "matches": [seg for seg in t.segments if needle in normalize(seg["text"])][:10]}
        for t in rows
    ]


@router.get("/v1/analytics/summary", tags=["analytics"])
async def analytics_summary(request: Request, version: str | None = None) -> dict[str, Any]:
    st = _st(request)
    version = version or st.settings.analyzer_version
    async with st.sessions() as s:
        statuses = dict((await s.execute(
            select(Call.status, func.count()).group_by(Call.status))).all())
        qa = (await s.execute(
            select(func.count(), func.avg(QAResult.score),
                   func.sum(case((QAResult.compliance == "violation", 1), else_=0)))
            .where(QAResult.analyzer_version == version))).one()
        flags_sub = (select(func.unnest(QAResult.flags).label("flag"))
                     .where(QAResult.analyzer_version == version).subquery())
        flags = dict((await s.execute(
            select(flags_sub.c.flag, func.count()).group_by(flags_sub.c.flag))).all())
        latency = func.extract("epoch", Call.completed_at - Call.created_at)
        p50, p95 = (await s.execute(
            select(func.percentile_cont(0.5).within_group(latency),
                   func.percentile_cont(0.95).within_group(latency))
            .where(Call.completed_at.is_not(None)))).one()
    scored = qa[0] or 0
    return {
        "calls_by_status": statuses,
        "analyzer_version": version,
        "scored_calls": scored,
        "average_score": round(qa[1], 1) if qa[1] is not None else None,
        "compliance_violations": qa[2] or 0,
        "flag_counts": flags,
        "flag_rates": {f: round(n / scored, 3) for f, n in flags.items()} if scored else {},
        "end_to_end_seconds": {"p50": p50, "p95": p95},
        "outbox_backlog": await st.backlog.backlog(),
    }


# --------------------------------------------------------------------------- operations


@router.post("/v1/reprocess", response_model=sch.ReprocessResponse, tags=["operations"],
             summary="Re-run a stage (e.g. re-score with a new analyzer version)")
async def reprocess(request: Request, body: sch.ReprocessRequest) -> sch.ReprocessResponse:
    st = _st(request)
    await st.backlog.check()
    query = select(Call.id, Call.trace_id).order_by(Call.created_at).limit(body.limit)
    if body.call_ids:
        query = query.where(Call.id.in_(body.call_ids))
    elif body.status is not None:
        query = query.where(Call.status == body.status)
    async with st.sessions() as s, s.begin():
        rows = (await s.execute(query)).all()
        for call_id, trace_id in rows:
            enqueue_stage(s, StageMessage(call_id=call_id, stage=body.stage, trace_id=trace_id,
                                          origin="reprocess", force=body.force))
    log.info("reprocess enqueued", extra={"stage": body.stage, "calls": len(rows)})
    return sch.ReprocessResponse(stage=body.stage, enqueued=len(rows))


@router.post("/v1/admin/parking-lot/replay", response_model=sch.ReplayResponse,
             tags=["operations"], summary="Move parked messages back into the pipeline")
async def replay_parking_lot(
    request: Request,
    call_id: Annotated[list[UUID] | None, Query()] = None,
    reason: Annotated[list[str] | None, Query()] = None,
    include_poison: bool = False,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
) -> sch.ReplayResponse:
    """Selective replay. Messages RabbitMQ dead-lettered for exceeding the delivery limit
    crashed workers repeatedly; replaying them blindly re-injects the poison, so they are
    skipped unless `include_poison` or an explicit `call_id` asks for them. Non-matching
    messages are rotated back into the parking lot, never dropped."""
    st = _st(request)
    wanted_calls = set(call_id or [])
    channel = await st.amqp.channel(publisher_confirms=True, on_return_raises=True)
    replayed, skipped, discarded, call_ids = 0, 0, 0, []
    try:
        queue = await channel.declare_queue(PARKING_QUEUE, passive=True)
        pending = queue.declaration_result.message_count  # visit each message at most once
        pipeline = await channel.get_exchange(EXCHANGE_PIPELINE)
        for _ in range(pending):
            if replayed >= limit:
                break
            message = await queue.get(no_ack=False, fail=False)
            if message is None:
                break
            try:
                env = StageMessage.model_validate_json(message.body)
            except ValidationError:
                await message.ack()
                discarded += 1
                continue
            why = (header_str(message.headers, "x-callintel-reason")
                   or header_str(message.headers, "x-first-death-reason") or "unknown")
            selected = (
                (not wanted_calls or env.call_id in wanted_calls)
                and (not reason or why in reason)
                and (why != "delivery_limit" or include_poison or env.call_id in wanted_calls)
            )
            if not selected:
                # Back of the parking lot, headers intact; ack only after the broker has it.
                await channel.default_exchange.publish(
                    aio_pika.Message(body=message.body, headers=message.headers,
                                     content_type=message.content_type,
                                     delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                    routing_key=PARKING_QUEUE, mandatory=True,
                )
                await message.ack()
                skipped += 1
                continue
            fresh = env.model_copy(update={"message_id": uuid4(), "attempt": 1,
                                           "origin": "replay"})
            await pipeline.publish(
                to_amqp(fresh, message_id=str(fresh.message_id),
                        headers={"x-trace-id": fresh.trace_id}),
                routing_key=fresh.stage.value, mandatory=True,
            )
            await message.ack()  # only after the broker confirmed the republish
            replayed += 1
            call_ids.append(env.call_id)
    finally:
        await channel.close()
    log.info("parking lot replayed", extra={"replayed": replayed, "skipped": skipped,
                                            "discarded": discarded})
    return sch.ReplayResponse(replayed=replayed, skipped=skipped, discarded=discarded,
                              call_ids=call_ids)


@router.get("/v1/events", tags=["events"], summary="Server-sent events for live dashboards")
async def events(request: Request) -> StreamingResponse:
    broadcaster = _st(request).broadcaster
    queue = broadcaster.subscribe()

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {data}\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- health


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    """Database and storage are required to accept uploads; the broker is not (the outbox
    buffers), so a broker outage reports 'degraded' but stays ready."""
    st = _st(request)
    checks: dict[str, str] = {}
    try:
        async with st.sessions() as s:
            await s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await st.store.ping()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"error: {type(exc).__name__}"
    checks["broker"] = "error: disconnected" if st.amqp.is_closed else "ok"
    ready = checks["database"] == "ok" and checks["storage"] == "ok"
    status = "ok" if ready and checks["broker"] == "ok" else ("degraded" if ready else "down")
    return JSONResponse({"status": status, "checks": checks}, status_code=200 if ready else 503)
