"""Fault-injection suite. Every row of docs/failure-modes.md names the scenario that proves it.

    uv run python scripts/chaos.py                 # all fast scenarios
    uv run python scripts/chaos.py kill-asr-worker broker-outage
    uv run python scripts/chaos.py --slow          # also the multi-minute ones

Needs the compose stack (make up) and a generated dataset (make data). Scenarios restart
containers; run it against a local stack only.
"""

import argparse
import asyncio
import os
import random
import struct
import sys
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from _common import (
    API,
    ROOT,
    compose,
    get_call,
    load_manifest,
    mgmt_publish,
    queue_messages,
    stage,
    upload,
    wait_for,
    wait_queue_growth,
)
from _common import fresh_variant as _fresh

DATASET = Path(os.environ.get("DATASET", "data/synthetic"))
PARKING = "callintel.parking-lot"


class Failed(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


@dataclass
class Ctx:
    client: httpx.AsyncClient
    manifest: list[dict]
    notes: list[str] = field(default_factory=list)

    def entry(self, name: str | None = None, *, outcome: str = "completed") -> dict:
        pool = [e for e in self.manifest if (e["name"] == name if name
                                             else not e["name"].startswith("edge_"))
                and e["expected_outcome"] == outcome]
        check(bool(pool), f"dataset has no entry {name or ''}; run `make data`")
        return random.choice(pool)

    def fresh(self, name: str | None = None) -> tuple[bytes, str, str]:
        entry = self.entry(name, outcome="failed" if name == "edge_poison_crash" else "completed")
        tag = f"chaos-{entry['name']}-{uuid.uuid4().hex[:6]}"
        content, _ = _fresh(DATASET, entry, tag)
        return content, tag, "audio/mpeg" if entry["file"].endswith(".mp3") else "audio/wav"

    async def submit(self, name: str | None = None) -> str:
        content, tag, media = self.fresh(name)
        await asyncio.sleep(1.1)  # let the mock ASR's ground-truth index notice the new file
        r = await upload(self.client, content, name=tag, media_type=media)
        check(r.status_code == 202, f"upload returned {r.status_code}: {r.text[:200]}")
        return r.json()["id"]

    def note(self, text: str) -> None:
        self.notes.append(text)


Scenario = Callable[[Ctx], Awaitable[None]]
SCENARIOS: dict[str, tuple[Scenario, bool]] = {}


def scenario(name: str, *, slow: bool = False) -> Callable[[Scenario], Scenario]:
    def register(fn: Scenario) -> Scenario:
        SCENARIOS[name] = (fn, slow)
        return fn
    return register


async def asr_running(ctx: Ctx, call_id: str) -> None:
    await wait_for(ctx.client, call_id, timeout=60, predicate=lambda c: (s := stage(c, "asr"))
                   is not None and s["status"] == "running")
    await asyncio.sleep(2)  # well inside the transcription, not at its edge


# --------------------------------------------------------------------------- ingestion


@scenario("duplicate-upload")
async def duplicate_upload(ctx: Ctx) -> None:
    content, tag, media = ctx.fresh()
    a, b = await asyncio.gather(upload(ctx.client, content, name=tag, media_type=media),
                                upload(ctx.client, content, name=tag, media_type=media))
    codes = sorted([a.status_code, b.status_code])
    check(codes == [200, 202], f"expected one 202 and one 200, got {codes}")
    check(a.json()["id"] == b.json()["id"], "the two uploads produced different call ids")
    ctx.note("two concurrent identical uploads -> one call (sha256 unique constraint)")


@scenario("upload-validation")
async def upload_validation(ctx: Ctx) -> None:
    r = await upload(ctx.client, b"plain text, definitely not audio" * 10, name="chaos-text")
    check(r.status_code == 415, f"text body: expected 415, got {r.status_code}")
    r = await upload(ctx.client, b"", name="chaos-empty")
    check(r.status_code == 400, f"empty body: expected 400, got {r.status_code}")

    async def huge():  # chunked, no Content-Length: the limit must be enforced while streaming
        yield b"RIFF\xff\xff\xff\xffWAVEfmt "
        chunk = b"\0" * (1 << 20)
        for _ in range(260):
            yield chunk

    started = time.monotonic()
    r = await ctx.client.post("/v1/calls", params={"external_id": "chaos-huge"}, content=huge(),
                              headers={"Content-Type": "audio/wav"})
    check(r.status_code == 413, f"oversized stream: expected 413, got {r.status_code}")
    ctx.note(f"415/400/413 as expected; 260 MB stream rejected in {time.monotonic() - started:.1f}s")


@scenario("corrupt-file")
async def corrupt_file(ctx: Ctx) -> None:
    payload = random.randbytes(32_000)
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 0x7777, 2, 8000, 32000, 4, 16)
    data = struct.pack("<4sI", b"data", len(payload)) + payload
    body = struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(data), b"WAVE") + fmt + data
    parked_before = queue_messages(PARKING)
    r = await upload(ctx.client, body, name=f"chaos-corrupt-{uuid.uuid4().hex[:6]}")
    check(r.status_code == 202, f"corrupt file should pass the sniff, got {r.status_code}")
    call = await wait_for(ctx.client, r.json()["id"], timeout=30)
    check(call["status"] == "failed", f"expected failed, got {call['status']}")
    pre = stage(call, "preprocess")
    check(pre is not None and pre["attempts"] == 1,
          f"permanent errors must not be retried; attempts={pre and pre['attempts']}")
    parked = await wait_queue_growth(PARKING, parked_before)
    check(parked > parked_before, "message did not reach the parking lot")
    ctx.note(f"failed after 1 attempt, parked: {call['error'][:80]}")


# --------------------------------------------------------------------------- workers


@scenario("kill-asr-worker")
async def kill_asr_worker(ctx: Ctx) -> None:
    call_id = await ctx.submit("edge_long_call")
    await asr_running(ctx, call_id)
    compose("kill", "-s", "SIGKILL", "worker-asr")
    compose("up", "-d", "--no-deps", "worker-asr")
    call = await wait_for(ctx.client, call_id, timeout=240)
    check(call["status"] == "completed", f"expected completed, got {call['status']}")
    asr = stage(call, "asr")
    check(asr["attempts"] >= 2, f"expected a redelivery, attempts={asr['attempts']}")
    check(len(call["qa_results"]) == 1, "duplicate results were committed")
    ctx.note(f"SIGKILL mid-transcription -> redelivered, completed, asr attempts={asr['attempts']}")


@scenario("graceful-shutdown")
async def graceful_shutdown(ctx: Ctx) -> None:
    call_id = await ctx.submit("edge_long_call")
    await asr_running(ctx, call_id)
    started = time.monotonic()
    compose("stop", "-t", "120", "worker-asr")  # SIGTERM; returns when the container exited
    stopped_after = time.monotonic() - started
    call = await get_call(ctx.client, call_id)
    asr = stage(call, "asr")
    compose("up", "-d", "--no-deps", "worker-asr")
    check(asr["status"] == "succeeded" and asr["attempts"] == 1,
          f"in-flight work should finish before exit; asr={asr['status']}/{asr['attempts']}")
    call = await wait_for(ctx.client, call_id, timeout=120)
    check(call["status"] == "completed", f"expected completed, got {call['status']}")
    ctx.note(f"SIGTERM drained the in-flight call ({stopped_after:.0f}s) with 1 attempt")


@scenario("poison-message")
async def poison_message(ctx: Ctx) -> None:
    call_id = await ctx.submit("edge_poison_crash")
    call = await wait_for(ctx.client, call_id, timeout=300)
    check(call["status"] == "failed", f"expected failed, got {call['status']}")
    check("delivery limit" in (call["error"] or ""), f"unexpected error: {call['error']}")
    compose("up", "-d", "--no-deps", "worker-asr")  # in case the restart backoff is still waiting
    ctx.note("worker crashed on every delivery; quorum delivery-limit dead-lettered it")


@scenario("replay-skips-poison")
async def replay_skips_poison(ctx: Ctx) -> None:
    """Runs after poison-message: a blind replay would re-inject the poison and crash workers."""
    before = queue_messages(PARKING)
    r = await ctx.client.post("/v1/admin/parking-lot/replay",
                              params={"reason": "delivery_limit"})
    body = r.json()
    check(body["replayed"] == 0, f"poison was replayed: {body}")
    check(body["skipped"] >= 1, f"expected parked poison to be skipped: {body}")
    await asyncio.sleep(6)  # management API counters refresh every ~5 s
    check(queue_messages(PARKING) >= before, "skipped messages were lost from the parking lot")
    ctx.note(f"{body['skipped']} parked message(s) skipped and rotated back; 0 poison replayed")


@scenario("duplicate-delivery")
async def duplicate_delivery(ctx: Ctx) -> None:
    call_id = await ctx.submit()
    call = await wait_for(ctx.client, call_id, timeout=120)
    check(call["status"] == "completed", f"setup failed: {call['status']}")
    before = stage(call, "asr")["attempts"]
    routed = mgmt_publish("callintel.pipeline", "asr", {
        "message_id": str(uuid.uuid4()), "call_id": call_id, "stage": "asr",
        "trace_id": call["trace_id"]})
    check(routed, "duplicate was not routed")
    await asyncio.sleep(5)
    call = await get_call(ctx.client, call_id)
    check(stage(call, "asr")["attempts"] == before, "duplicate delivery re-ran the stage")
    check(call["status"] == "completed", f"status changed to {call['status']}")
    ctx.note("redelivered asr message acknowledged and skipped (stage_runs idempotency)")


# --------------------------------------------------------------------------- dependencies


@scenario("broker-outage")
async def broker_outage(ctx: Ctx) -> None:
    compose("stop", "rabbitmq")
    try:
        ids = [await ctx.submit() for _ in range(3)]  # gateway does not need the broker
        call = await get_call(ctx.client, ids[0])
        check(call["status"] == "received", f"expected received, got {call['status']}")
    finally:
        compose("up", "-d", "--no-deps", "rabbitmq")
    calls = [await wait_for(ctx.client, i, timeout=240) for i in ids]
    check(all(c["status"] == "completed" for c in calls), "not all calls completed")
    ctx.note("3 uploads accepted with RabbitMQ down; all completed after it returned")


@scenario("relay-outage")
async def relay_outage(ctx: Ctx) -> None:
    compose("stop", "outbox-relay")
    try:
        ids = [await ctx.submit() for _ in range(2)]
        summary = (await ctx.client.get("/v1/analytics/summary")).json()
        check(summary["outbox_backlog"] >= 2, f"backlog={summary['outbox_backlog']}")
        backlog = summary["outbox_backlog"]
    finally:
        compose("up", "-d", "--no-deps", "outbox-relay")
    calls = [await wait_for(ctx.client, i, timeout=120) for i in ids]
    check(all(c["status"] == "completed" for c in calls), "not all calls completed")
    ctx.note(f"outbox held {backlog} rows while the relay was down; drained on restart")


@scenario("storage-outage")
async def storage_outage(ctx: Ctx) -> None:
    compose("stop", "outbox-relay")
    try:
        call_id = await ctx.submit()
        compose("stop", "objectstore")
    finally:
        compose("up", "-d", "--no-deps", "outbox-relay")  # `start` would revive objectstore
    try:
        await wait_for(ctx.client, call_id, timeout=60, predicate=lambda c: (
            s := stage(c, "preprocess")) is not None and s["status"] == "retrying")
    finally:
        await asyncio.sleep(3)
        compose("up", "-d", "--no-deps", "objectstore")
    call = await wait_for(ctx.client, call_id, timeout=120)
    check(call["status"] == "completed", f"expected completed, got {call['status']}")
    attempts = stage(call, "preprocess")["attempts"]
    check(attempts >= 2, f"expected a retry, attempts={attempts}")
    ctx.note(f"object storage down -> preprocess retried with backoff, attempts={attempts}")


@scenario("retries-exhausted-then-replay", slow=True)
async def exhausted_then_replay(ctx: Ctx) -> None:
    compose("stop", "outbox-relay")
    try:
        call_id = await ctx.submit()
        compose("stop", "objectstore")
    finally:
        compose("up", "-d", "--no-deps", "outbox-relay")  # `start` would revive objectstore
    try:
        call = await wait_for(ctx.client, call_id, timeout=400)  # 5 + 30 + 120 s of backoff
        check(call["status"] == "failed", f"expected failed, got {call['status']}")
    finally:
        compose("up", "-d", "--no-deps", "objectstore")
    await asyncio.sleep(3)
    r = await ctx.client.post("/v1/admin/parking-lot/replay", params={"call_id": call_id})
    check(call_id in r.json()["call_ids"], "call was not in the parking lot")
    # The call still reads `failed` until a worker claims the replayed message.
    call = await wait_for(ctx.client, call_id, timeout=120,
                          predicate=lambda c: c["status"] == "completed")
    ctx.note("4 attempts exhausted -> parked -> storage fixed -> replayed -> completed")


# --------------------------------------------------------------------------- runner


async def main(args: argparse.Namespace) -> int:
    names = args.names or [n for n, (_, slow) in SCENARIOS.items() if args.slow or not slow]
    unknown = set(names) - set(SCENARIOS)
    if unknown:
        print(f"unknown scenarios: {', '.join(sorted(unknown))}; known: {', '.join(SCENARIOS)}")
        return 2
    results = []
    async with httpx.AsyncClient(base_url=API, timeout=300) as client:
        ctx_manifest = load_manifest(DATASET)
        for name in names:
            ctx = Ctx(client, ctx_manifest)
            print(f"▶ {name} ...", flush=True)
            started = time.monotonic()
            try:
                await SCENARIOS[name][0](ctx)
                ok, detail = True, "; ".join(ctx.notes)
            except Exception as exc:
                ok = False
                detail = str(exc) if isinstance(exc, Failed | TimeoutError) else (
                    traceback.format_exc(limit=3))
            elapsed = time.monotonic() - started
            results.append((name, ok, elapsed, detail))
            print(f"  {'PASS' if ok else 'FAIL'} ({elapsed:.0f}s) {detail}", flush=True)
            compose("up", "-d", "--wait", check=False)  # leave the stack whole for the next one

    table = ["| scenario | result | time | evidence |", "|---|---|---|---|"]
    table += [f"| {name} | {'PASS' if ok else 'FAIL'} | {elapsed:.0f}s | "
              f"{detail.splitlines()[-1] if detail else ''} |"
              for name, ok, elapsed, detail in results]
    print("\n" + "\n".join(table))
    if not args.names:  # a full run: record it next to the failure-mode table
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        (ROOT / "docs" / "chaos-results.md").write_text(
            f"# Chaos results\n\nLast full run: {stamp}, `scripts/chaos.py"
            f"{' --slow' if args.slow else ''}` against the local compose stack.\n\n"
            + "\n".join(table) + "\n")
    return 0 if all(ok for _, ok, _, _ in results) else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*")
    p.add_argument("--slow", action="store_true", help="include multi-minute scenarios")
    sys.exit(asyncio.run(main(p.parse_args())))
