"""Throughput test: upload a dataset as fresh calls, wait for the pipeline to drain, report.

    uv run python scripts/loadtest.py --data data/load --label "4 ASR workers"

Run `docker compose up -d --scale worker-asr=N` between runs to compare scaling.
Results are appended to docs/benchmarks.md.
"""

import argparse
import asyncio
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from _common import API, ROOT, fresh_variant, load_manifest, percentile, upload


def asr_replicas() -> int:
    out = subprocess.run(["docker", "compose", "ps", "-q", "worker-asr"], cwd=ROOT,
                         capture_output=True, text=True)
    return len(out.stdout.split())


async def main(args: argparse.Namespace) -> None:
    dataset = Path(args.data)
    entries = [e for e in load_manifest(dataset) if e["expected_outcome"] == "completed"]
    entries = entries[: args.limit] if args.limit else entries
    run = uuid.uuid4().hex[:6]
    print(f"run {run}: preparing {len(entries)} fresh variants ...", flush=True)
    payloads = [(f"load-{run}-{e['name']}", *fresh_variant(dataset, e, f"load-{run}-{e['name']}"),
                 e.get("duration_s") or 0) for e in entries]
    await asyncio.sleep(1.5)

    sem = asyncio.Semaphore(args.concurrency)
    upload_s: list[float] = []
    rejected = 0
    async with httpx.AsyncClient(base_url=API, timeout=300) as client:
        async def send(name: str, content: bytes) -> None:
            nonlocal rejected
            async with sem:
                t0 = time.perf_counter()
                r = await upload(client, content, name=name,
                                 media_type="audio/mpeg" if content[:3] == b"ID3"
                                 or content[:1] == b"\xff" else "audio/wav")
                upload_s.append(time.perf_counter() - t0)
                if r.status_code != 202:
                    rejected += 1

        started = time.monotonic()
        await asyncio.gather(*(send(name, content) for name, content, _, _ in payloads))
        uploaded_in = time.monotonic() - started
        print(f"uploaded {len(payloads)} in {uploaded_in:.1f}s; waiting for the pipeline ...")

        while True:
            calls = (await client.get("/v1/calls", params={"external_prefix": f"load-{run}-",
                                                           "limit": 1000})).json()
            done = [c for c in calls if c["status"] in ("completed", "failed")]
            print(f"\r  {len(done)}/{len(payloads)} done", end="", flush=True)
            if len(done) >= len(payloads) - rejected:
                break
            await asyncio.sleep(2)
        wall = time.monotonic() - started
    print()

    def ts(v: str) -> datetime:
        return datetime.fromisoformat(v)

    e2e = [(ts(c["completed_at"]) - ts(c["created_at"])).total_seconds()
           for c in calls if c["completed_at"]]
    failed = sum(c["status"] == "failed" for c in calls)
    audio_min = sum(d for *_, d in payloads) / 60
    row = (f"| {datetime.now(UTC):%Y-%m-%d %H:%M} | {args.label or '-'} | {asr_replicas()} | "
           f"{len(payloads)} | {audio_min:.0f} | {wall:.0f}s | {len(payloads) / wall * 60:.1f} | "
           f"{audio_min / (wall / 60):.1f} | {percentile(upload_s, 0.95):.2f}s | "
           f"{percentile(e2e, 0.5):.0f}s | {percentile(e2e, 0.95):.0f}s | {failed + rejected} |")
    header = ("| when (UTC) | label | ASR workers | calls | audio min | wall | calls/min | "
              "audio min/min | upload p95 | e2e p50 | e2e p95 | failed |\n"
              "|---|---|---|---|---|---|---|---|---|---|---|---|")
    print(header + "\n" + row)
    out = ROOT / "docs" / "benchmarks.md"
    if not out.exists():
        out.write_text("# Benchmarks\n\nProduced by `scripts/loadtest.py` against the local "
                       "compose stack (mock ASR; see the label for MOCK_ASR_RTF).\n\n"
                       + header + "\n")
    with out.open("a") as fh:
        fh.write(row + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/load")
    p.add_argument("--concurrency", type=int, default=16)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--label", default="")
    asyncio.run(main(p.parse_args()))
