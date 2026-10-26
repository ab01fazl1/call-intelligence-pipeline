"""Upload a generated dataset to the running gateway.

    uv run python scripts/upload.py data/synthetic [--edge-cases] [--concurrency 8]
"""

import argparse
import asyncio
from collections import Counter
from pathlib import Path

import httpx
from _common import API, load_manifest, upload


async def main(args: argparse.Namespace) -> None:
    dataset = Path(args.dataset)
    entries = [e for e in load_manifest(dataset)
               if args.edge_cases or not e["name"].startswith("edge_")]
    sem = asyncio.Semaphore(args.concurrency)
    results: Counter[str] = Counter()

    async with httpx.AsyncClient(base_url=API, timeout=120) as client:
        async def one(entry):
            async with sem:
                r = await upload(client, dataset / entry["file"], name=entry["name"])
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            kind = "duplicate" if body.get("duplicate") else str(r.status_code)
            results[kind] += 1
            print(f"{r.status_code}  {entry['name']:24} {body.get('id', body.get('detail', ''))}")

        await asyncio.gather(*(one(e) for e in entries))
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(results.items())))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", nargs="?", default="data/synthetic")
    p.add_argument("--edge-cases", action="store_true")
    p.add_argument("--concurrency", type=int, default=8)
    asyncio.run(main(p.parse_args()))
