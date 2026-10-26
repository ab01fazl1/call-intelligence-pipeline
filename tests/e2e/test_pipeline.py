"""End-to-end tests against the running compose stack.

    make up && make data && uv run pytest -m e2e
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

API = os.environ.get("API_URL", "http://localhost:8000")
DATASET = Path(os.environ.get("DATASET", "data/synthetic"))


def _entry(name: str) -> dict:
    manifest = json.loads((DATASET / "manifest.json").read_text())
    return next(e for e in manifest if e["name"] == name)


async def _wait(client: httpx.AsyncClient, call_id: str) -> dict:
    async with asyncio.timeout(120):
        while True:
            call = (await client.get(f"/v1/calls/{call_id}")).json()
            if call["status"] in ("completed", "failed"):
                return call
            await asyncio.sleep(0.5)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(base_url=API, timeout=60) as c:
        yield c


async def test_call_is_scored_with_its_expected_flags(client):
    entry = _entry("call_0011")
    body = (DATASET / entry["file"]).read_bytes()
    r = await client.post("/v1/calls", params={"external_id": entry["name"]}, content=body,
                          headers={"Content-Type": "audio/wav"})
    assert r.status_code in (200, 202)  # 200 if the dataset was uploaded before
    call = await _wait(client, r.json()["id"])
    assert call["status"] == "completed"
    # The database may hold earlier runs (reprocessing); every stage must have succeeded.
    assert {s["stage"] for s in call["stages"] if s["status"] == "succeeded"} == {
        "preprocess", "asr", "analysis"}
    assert call["analyzer_version"] == "v3"  # summary fields use the default version
    assert sorted(call["flags"]) == entry["expected_flags"]


async def test_reupload_is_deduplicated(client):
    entry = _entry("call_0012")
    body = (DATASET / entry["file"]).read_bytes()
    first = await client.post("/v1/calls", content=body, headers={"Content-Type": "audio/wav"})
    second = await client.post("/v1/calls", content=body, headers={"Content-Type": "audio/wav"})
    assert second.status_code == 200 and second.json()["duplicate"]
    assert first.json()["id"] == second.json()["id"]


async def test_search_finds_normalized_persian(client):
    await test_call_is_scored_with_its_expected_flags(client)
    # Query typed with Arabic yeh (ي) must match the Persian yeh (ی) in the transcript.
    hits = (await client.get("/v1/search", params={"q": "معاملات آنلاين"})).json()
    assert hits and all(h["matches"] for h in hits)
