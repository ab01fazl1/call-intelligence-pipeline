# Failure modes

What happens when each part breaks, how the system recovers, and how we know. The **Verified
by** column names the automated check: a `chaos:` scenario (`scripts/chaos.py <name>`, run
against the live stack), a `unit:` test, or *code path only* where no automated test exists
yet. The latest chaos run is recorded in [chaos-results.md](chaos-results.md).

Guarantees the table relies on: ack after commit (ADR 0003), every publish via the outbox
(ADR 0002), idempotency per (call, stage, version) (ADR 0003), and typed retry/park policy
(ADR 0004).

## Ingestion

| # | Failure | What the system does | Data loss | Verified by |
|---|---|---|---|---|
| 1 | PBX re-sends the same recording (retry, double export) | SHA-256 unique constraint; the second upload returns the existing call with `200`, and its object is deleted. A concurrent race is resolved by the `IntegrityError` path. | none | chaos: `duplicate-upload` (two simultaneous uploads) |
| 2 | Body is not audio / empty / larger than the limit | Magic-byte sniff rejects non-audio in the first 12 bytes (`415`); empty → `400`; the size limit is enforced on received bytes while streaming (`413`), even without Content-Length. The multipart upload is aborted. | n/a | chaos: `upload-validation` (260 MB chunked stream) · unit: `test_sniff`, `test_upload_body_limits` |
| 3 | Client disconnects mid-upload | `ClientDisconnect` aborts the S3 multipart upload; no call row is created. | n/a (client retries) | code path only (`ObjectStore.upload_stream`) |
| 4 | Database unavailable while registering an upload | The stored object is deleted and the client gets `503 Retry-After: 10`. No orphaned objects. | none (client retries) | code path only |
| 5 | Object storage unavailable | `/readyz` reports down (load balancer stops routing); uploads fail fast. Stages already queued retry with backoff (row 12). | none | chaos: `storage-outage` (worker side) |

## Processing

| # | Failure | What the system does | Data loss | Verified by |
|---|---|---|---|---|
| 6 | Corrupt / undecodable audio | `CorruptAudioError` is permanent: parked after **one** attempt (no retry budget burnt), call `failed` with the decoder's reason. | n/a | chaos: `corrupt-file` · unit: `test_preprocess_rejects_garbage` |
| 7 | Too short / too long / more than 2 channels / below 8 kHz | `UnsupportedAudioError` (permanent) with a precise reason. The duration limit is checked from the header, then again while streaming, for files that lie. | n/a | unit: `test_preprocess_rejects_too_short` |
| 8 | Worker killed mid-message (SIGKILL, OOM, node loss) | The message was never acked, so RabbitMQ redelivers it. The next claim increments `attempts`; the completion compare-and-set keeps results single. | none | chaos: `kill-asr-worker` |
| 9 | Deploy / scale-in (SIGTERM) | The worker cancels its consumer, finishes in-flight messages within `SHUTDOWN_GRACE_S`, then exits. Anything still running after the grace period is cancelled unacked and redelivered. | none | chaos: `graceful-shutdown` (completes with 1 attempt) |
| 10 | Poison message: processing crashes the process every time | Quorum queue `x-delivery-limit=5` dead-letters it to the DLX → parking lot + `dead-letters` consumer marks the call `failed: delivery limit exceeded`. **Blast radius:** other messages held by the dying worker are redelivered and count toward their own limit, hence ASR prefetch 1. | none (parked) | chaos: `poison-message` (mock ASR calls `os._exit` on a marked file) |
| 11 | Duplicate delivery (relay republish, operator replay) | Claim finds the stage `succeeded` for this version → ack and skip, no work done. | n/a | chaos: `duplicate-delivery` |
| 12 | Transient dependency error (storage blip, timeout) | `TransientError` → delay queues 5 s → 30 s → 120 s (broker-side TTL; no worker is blocked), `stage_runs.status = retrying` with the last error. | none | chaos: `storage-outage` |
| 13 | Dependency down longer than the retry budget | Parked with `retries_exhausted`; call `failed` with the last error. After the fix, `POST /v1/admin/parking-lot/replay?call_id=…&reason=…` republishes matching messages with a fresh attempt budget; the rest rotate back into the parking lot. | none (parked) | chaos: `retries-exhausted-then-replay` (`--slow`, ~3 min) |
| 13b | Operator replays the parking lot while it contains poison | Replay is selective: messages RabbitMQ dead-lettered for the delivery limit are skipped unless `include_poison=true` or named by `call_id`. (Found the hard way: a blind replay re-injected a poison message, which crash-looped the ASR worker and blocked the calls queued behind it until the delivery limit caught it again.) | none | chaos: `replay-skips-poison` |
| 14 | Two deliveries of the same message processed concurrently | Both may compute; the completion compare-and-set (`status != 'succeeded'`) lets exactly one commit. The other discards its result. Lock order call → stage_run prevents deadlocks. | none | code path only (`StageWorker._complete`); timing-dependent |
| 15 | ASR hangs or is far slower than expected | Timeout = `max(60 s, duration × channels × ASR_TIMEOUT_RTF)`. On timeout the Whisper child process is terminated and recreated, and the message follows the retry policy. RabbitMQ's `consumer_timeout` (1 h) is deliberately longer, so the worker's timeout fires first. | none | code path only |
| 16 | Inference process dies (native segfault inside CTranslate2) | `BrokenProcessPool` → `TransientError`, pool rebuilt, retried. If the whole worker dies instead, row 10 applies. | none | code path only (row 10 covers whole-process death) |
| 16b | A reprocess or replay fails for a call that had completed (e.g. a new ASR model crashes on it) | The stage run is marked failed with the error and a `reprocess.failed` event is emitted, but the call stays `completed`: its previous results remain valid. (Found when a PyAV incompatibility made every Whisper re-transcription fail and the first version marked 10 good calls `failed`.) | none | code path only (seen live; not an automated scenario) |
| 17 | Malformed message on a work queue | Parked with reason `malformed`; the consumer never crashes on bad input (tolerant reader ignores unknown fields). | n/a | unit: `test_stage_message_ignores_unknown_fields`; parking path code only |

## Infrastructure

| # | Failure | What the system does | Data loss | Verified by |
|---|---|---|---|---|
| 18 | RabbitMQ down | Gateway keeps accepting uploads (it writes only to Postgres + storage), so pending work accumulates in the outbox. Workers and the relay reconnect automatically (robust connections). The backlog drains on recovery. `/readyz` reports `degraded`, not down. | none | chaos: `broker-outage` (3 uploads accepted while it is down) |
| 19 | Outbox relay down, or crashes between publish and mark | Rows wait in Postgres; on restart they are published (possibly twice, which is harmless, see row 11). `SKIP LOCKED` allows several relays. | none | chaos: `relay-outage` |
| 20 | Postgres down | Gateway returns 503; worker claims fail and follow the retry policy; the relay retries its loop; LISTEN reconnects, with polling as fallback. | none (work waits in queues) | code path only (manual: `docker compose stop postgres`) |
| 21 | Burst larger than capacity | Bounded stage queues (`reject-publish`) nack the relay → rows stay in the outbox with backoff → above `OUTBOX_BACKLOG_LIMIT` the gateway sheds load with `503 Retry-After: 30`. | none | partially: `scripts/loadtest.py` measures drain; the 503 threshold is not asserted |
| 22 | Gateway replica dies | Stateless: other replicas serve. Dashboard SSE clients reconnect (`retry: 3000`) and reload state from the API; live events are best-effort by design. | none | code path only |

## Known gaps (would be next)

- Rows marked *code path only* deserve integration tests against real containers
  (testcontainers), especially 3, 4, 14 and 20.
- RabbitMQ runs as a single node here. Quorum queues are designed for three nodes; a single
  node gives the semantics (delivery limits, durable at-least-once dead-lettering) but not the
  availability.
- No alerting rules yet. Obvious first alerts: parking lot depth > 0, outbox backlog growth
  rate, ASR queue age, and the p95 end-to-end time.
