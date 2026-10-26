# 0004: Retry tiers, delivery limit and a parking lot

Status: accepted · 2026-10-26

## Context
There are three kinds of failure, and each needs different handling:

1. **Transient**: storage briefly down, a timeout. Retrying later will probably work.
2. **Permanent**: corrupt audio, a 0.3-second file. Retrying wastes capacity forever.
3. **Poison**: the message kills the worker process itself (native segfault, OOM). The
   worker never gets the chance to catch an exception, so app-level retry logic never runs.

`nack(requeue=True)` is the naive answer to all three and the source of hot redelivery loops.

## Decision
- Errors are typed (`TransientError`, `PermanentError` and subclasses). Unknown exceptions
  count as transient. `workers/policy.py` is a pure function: `(attempt, error) -> Retry|Park`.
- **Transient**: the worker republishes the message to a delay queue
  (`callintel.<stage>.retry.{5,30,120}s`, TTL plus dead-letter back to the work queue),
  then acks the original. Attempt 4 parks it (`retries_exhausted`).
- **Permanent**: parked immediately with `x-callintel-reason/error` headers.
- **Poison**: work queues are quorum queues with `x-delivery-limit: 5`. A message whose
  delivery kills the worker five times is dead-lettered by RabbitMQ itself.
- The DLX is a fan-out to two queues: `callintel.parking-lot` (kept for humans, replayable
  via `POST /v1/admin/parking-lot/replay`) and `callintel.dead-letters` (consumed: marks the
  call `failed` with the reason and emits an event).
- Replay is **selective** (`call_id`, `reason` filters). Poison messages (`delivery_limit`)
  are skipped unless asked for explicitly; non-matching messages rotate back into the
  parking lot. A blind "replay everything" was tried first and re-injected a poison message
  that crash-looped the ASR workers. The delivery limit caught it again, but calls queued
  behind it were delayed for minutes.
- Dead-lettering uses `x-dead-letter-strategy: at-least-once` (with `reject-publish`
  overflow), so a dead letter is never dropped when the target is momentarily unavailable.

## Consequences
+ Every failure ends somewhere visible with a reason; nothing loops forever
  (chaos: `corrupt-file`, `storage-outage`, `poison-message`, `retries-exhausted-then-replay`).
+ The retry schedule is broker-side: no sleeping workers holding prefetch slots.
- **Blast radius**: when a poison message kills a worker, the *other* messages that worker
  held are redelivered too and count toward their own delivery limit. That is why ASR
  workers use prefetch 1, and why the limit is 5, not 1.
- Changing queue arguments requires new queue names or RabbitMQ policies; declaring an
  existing queue with different arguments fails. In production these limits belong in
  policies managed by infrastructure code.

## Alternatives considered
- **Requeue with a sleep in the consumer**: blocks capacity and is not durable.
- **delayed-message-exchange plugin**: fine, but an extra plugin and less observable than
  fixed TTL queues (whose depth shows up in Grafana).
