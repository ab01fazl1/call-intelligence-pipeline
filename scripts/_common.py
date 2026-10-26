"""Shared helpers for the operator scripts (upload, chaos, loadtest)."""

import asyncio
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

API = os.environ.get("API_URL", "http://localhost:8000")
MGMT = os.environ.get("RABBITMQ_MGMT_URL", "http://localhost:15672")
MGMT_AUTH = ("callintel", "callintel")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONTENT_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
DONE = {"completed", "failed"}


def content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def load_manifest(dataset: Path) -> list[dict[str, Any]]:
    return json.loads((dataset / "manifest.json").read_text())


def fresh_variant(dataset: Path, entry: dict[str, Any], tag: str) -> tuple[bytes, str]:
    """Return the file's bytes with the last audio frame randomized, so it hashes differently
    and is processed again instead of being deduplicated. Ground truth (if any) is copied under
    the new hash into data/chaos/ground_truth, where the mock ASR finds it."""
    src = dataset / entry["file"]
    data = bytearray(src.read_bytes())
    if len(data) > 64:
        data[-4:] = os.urandom(4)
    content = bytes(data)
    sha = hashlib.sha256(content).hexdigest()
    gt_src = dataset / "ground_truth" / f"{entry['name']}.json"
    if gt_src.exists():
        truth = json.loads(gt_src.read_text())
        truth.update(sha256=sha, name=tag)
        out = DATA / "chaos" / "ground_truth"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tag}.json").write_text(json.dumps(truth, ensure_ascii=False))
    return content, sha


async def upload(client: httpx.AsyncClient, content: bytes | Path, *, name: str,
                 media_type: str = "audio/wav") -> httpx.Response:
    if isinstance(content, Path):
        media_type = content_type(content)
        content = content.read_bytes()
    return await client.post("/v1/calls", params={"external_id": name}, content=content,
                             headers={"Content-Type": media_type})


async def get_call(client: httpx.AsyncClient, call_id: str) -> dict[str, Any]:
    r = await client.get(f"/v1/calls/{call_id}")
    r.raise_for_status()
    return r.json()


async def wait_for(client: httpx.AsyncClient, call_id: str, *, statuses: set[str] = DONE,
                   timeout: float = 120, predicate=None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        try:
            call = await get_call(client, call_id)
            if (predicate(call) if predicate else call["status"] in statuses):
                return call
        except httpx.HTTPError:
            pass  # gateway may be restarting during chaos
        if time.monotonic() > deadline:
            raise TimeoutError(f"call {call_id} did not reach {statuses} in {timeout:.0f}s")
        await asyncio.sleep(0.5)


def stage(call: dict[str, Any], name: str) -> dict[str, Any] | None:
    runs = [s for s in call["stages"] if s["stage"] == name]
    return runs[-1] if runs else None


def compose(*args: str, check: bool = True) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=check,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def wait_queue_growth(name: str, before: int, timeout: float = 15) -> int:
    """Management API counters are refreshed every ~5 s; poll instead of reading once."""
    deadline = time.monotonic() + timeout
    while (now := queue_messages(name)) <= before and time.monotonic() < deadline:
        await asyncio.sleep(1)
    return now


def queue_messages(name: str) -> int:
    r = httpx.get(f"{MGMT}/api/queues/%2F/{name}", auth=MGMT_AUTH, timeout=10)
    r.raise_for_status()
    return int(r.json().get("messages", 0))


def mgmt_publish(exchange: str, routing_key: str, payload: dict[str, Any]) -> bool:
    r = httpx.post(f"{MGMT}/api/exchanges/%2F/{exchange}/publish", auth=MGMT_AUTH, timeout=10,
                   json={"properties": {"delivery_mode": 2, "content_type": "application/json",
                                        "message_id": str(uuid.uuid4())},
                         "routing_key": routing_key, "payload": json.dumps(payload),
                         "payload_encoding": "string"})
    r.raise_for_status()
    return bool(r.json().get("routed"))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)
