import uuid

import pytest

from callintel.errors import CorruptAudioError, TransientError
from callintel.eval.metrics import WER, Confusion, edit_distance
from callintel.gateway.upload import UnsupportedMedia, UploadBody, UploadTooLarge, sniff_audio
from callintel.messaging.envelope import Stage, StageMessage
from callintel.workers.policy import Park, Retry, decide


def test_retry_schedule_then_park():
    delays = [5, 30, 120]
    assert decide(1, TransientError(), delays) == Retry(5)
    assert decide(3, TimeoutError(), delays) == Retry(120)
    assert decide(4, TransientError(), delays) == Park("retries_exhausted")


def test_permanent_errors_skip_retries():
    assert decide(1, CorruptAudioError("x"), [5, 30]) == Park("permanent_error")


def test_unknown_errors_are_treated_as_transient():
    assert decide(1, KeyError("bug"), [5]) == Retry(5)


def test_stage_message_next_and_retry():
    msg = StageMessage(call_id=uuid.uuid4(), stage=Stage.PREPROCESS, trace_id="t")
    retry = msg.retry()
    assert retry.attempt == 2 and retry.message_id == msg.message_id
    nxt = retry.next(Stage.ASR)
    assert nxt.stage == Stage.ASR and nxt.attempt == 1 and nxt.message_id != msg.message_id
    assert nxt.trace_id == "t"


def test_stage_message_ignores_unknown_fields():
    raw = StageMessage(call_id=uuid.uuid4(), stage=Stage.ASR, trace_id="t").model_dump_json()
    parsed = StageMessage.model_validate_json(raw[:-1] + ', "future_field": 1}')
    assert parsed.stage == Stage.ASR


@pytest.mark.parametrize(("head", "kind"), [
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav"),
    (b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00", "audio/mpeg"),
    (b"\xff\xfb\x90\x64" + b"\x00" * 8, "audio/mpeg"),
    (b"hello world, not audio", None),
])
def test_sniff(head, kind):
    assert sniff_audio(head) == kind


async def _stream(*chunks):
    for c in chunks:
        yield c


async def _drain(body):
    return b"".join([c async for c in body.chunks()])


async def test_upload_body_hashes_and_counts_tiny_first_chunks():
    data = [b"RI", b"FF\x00\x00\x00\x00WA", b"VE", b"rest-of-file"]
    body = UploadBody(_stream(*data), max_bytes=1000)
    assert await _drain(body) == b"".join(data)
    assert body.size == len(b"".join(data)) and body.media_type == "audio/wav"


async def test_upload_body_limits():
    with pytest.raises(UploadTooLarge):
        await _drain(UploadBody(_stream(b"RIFF\x00\x00\x00\x00WAVE", b"x" * 100), max_bytes=50))
    with pytest.raises(UnsupportedMedia):
        await _drain(UploadBody(_stream(b"plain text body here"), max_bytes=1000))


def test_wer_and_confusion():
    assert edit_distance(["a", "b", "c"], ["a", "x", "c", "d"]) == 2
    w = WER()
    w.add("سلام وقت بخیر", "سلام وقت بخیر")
    assert w.value == 0
    c = Confusion()
    for expected, predicted in [(True, True), (True, False), (False, True), (False, False)]:
        c.add(expected, predicted)
    assert (c.precision, c.recall) == (0.5, 0.5)
