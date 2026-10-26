# Chaos results

Last full run: 2026-10-26 15:11 UTC, `scripts/chaos.py --slow` against the local compose stack.

| scenario | result | time | evidence |
|---|---|---|---|
| duplicate-upload | PASS | 0s | two concurrent identical uploads -> one call (sha256 unique constraint) |
| upload-validation | PASS | 2s | 415/400/413 as expected; 260 MB stream rejected in 1.6s |
| corrupt-file | PASS | 3s | failed after 1 attempt, parked: preprocess: CorruptAudioError: unreadable audio: Error opening '/tmp/pp-a2qztzy7 |
| kill-asr-worker | PASS | 48s | SIGKILL mid-transcription -> redelivered, completed, asr attempts=2 |
| graceful-shutdown | PASS | 44s | SIGTERM drained the in-flight call (39s) with 1 attempt |
| poison-message | PASS | 38s | worker crashed on every delivery; quorum delivery-limit dead-lettered it |
| replay-skips-poison | PASS | 6s | 9 parked message(s) skipped and rotated back; 0 poison replayed |
| duplicate-delivery | PASS | 17s | redelivered asr message acknowledged and skipped (stage_runs idempotency) |
| broker-outage | PASS | 46s | 3 uploads accepted with RabbitMQ down; all completed after it returned |
| relay-outage | PASS | 30s | outbox held 2 rows while the relay was down; drained on restart |
| storage-outage | PASS | 23s | object storage down -> preprocess retried with backoff, attempts=2 |
| retries-exhausted-then-replay | PASS | 194s | 4 attempts exhausted -> parked -> storage fixed -> replayed -> completed |
