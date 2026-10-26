# 0003: At-least-once delivery with idempotent stages

Status: accepted · 2026-10-26

## Context
Duplicates arrive from several directions: the relay republishing (ADR 0002), RabbitMQ
redelivering an unacknowledged message after a worker dies, a client re-uploading, an
operator replaying the parking lot. "Exactly-once delivery" is not on offer from any broker;
what we can build is exactly-once *effects*.

## Decision
- **Ack after commit.** A worker acknowledges a message only after its results are committed.
  If it dies before that, RabbitMQ redelivers the message. Nothing is lost.
- **Idempotency key = (call_id, stage, version)** in `stage_runs`:
  - *claim* (short transaction, locks the call row): if the stage already `succeeded` for
    this version, ack and skip; otherwise mark it `running` and increment `attempts`;
  - *run* outside any transaction (ASR can take minutes; never hold a transaction open);
  - *complete* (short transaction): compare-and-set `status != 'succeeded' -> 'succeeded'`.
    If the update matches no row, another delivery won the race, so this result is discarded.
    Otherwise results are upserted and the next stage is written to the outbox, all atomically.
- Both transactions lock the call row first, then the stage row, so they cannot deadlock.
- **Upload dedupe** by SHA-256 of the body (unique constraint). An identical upload returns
  the existing call with 200; a concurrent race is caught via the `IntegrityError`.

## Consequences
+ Duplicates are harmless and cheap: one indexed lookup and an ack
  (chaos: `duplicate-delivery`, `duplicate-upload`, `kill-asr-worker`).
+ `attempts` doubles as an audit trail of redeliveries.
- Work can be done twice (a redelivered ASR run repeats the transcription), but it is
  committed once. For expensive stages that is the right trade.
- Including `version` in the key means re-running a stage with a new model is a new unit of
  work by design (ADR 0011); re-running the *same* version requires `force=true`.
