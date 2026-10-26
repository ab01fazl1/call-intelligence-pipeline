# 0005: aio-pika workers instead of Celery

Status: accepted · 2026-10-26

## Context
The job ad names Celery-style asynchronous processing. Celery is the default choice for
Python background jobs, but the reliability design here depends on details Celery abstracts
away or makes awkward:

- acking **after** a database commit, and only then (`acks_late` exists, but its interaction
  with visibility, prefetch and worker loss needs care);
- quorum queues with `x-delivery-limit`, `x-overflow`, `x-dead-letter-strategy`;
- explicit retry queues and a dead-letter fan-out;
- an async stack end to end (FastAPI, SQLAlchemy async, aiobotocore). Celery workers are
  sync by default, so you end up running event loops inside tasks.

## Decision
Use **aio-pika** directly behind a small `StageWorker` base class (about 300 lines) that owns
the delivery semantics: claim, run, complete, ack; retry or park; graceful drain on SIGTERM.
Stage handlers implement only `run()` and `persist()`.

## Consequences
+ Every reliability property is explicit, testable, and visible in one file.
+ Native asyncio: one process handles many I/O-bound messages concurrently
  (prefetch = concurrency).
- We own code Celery would give us: no Flower, no canvas/chords, no beat scheduler.
  None of them is needed by a linear pipeline.
- New team members must learn the worker base instead of "just Celery".

## Alternatives considered
- **Celery + RabbitMQ**: the ecosystem is a plus; the fine-grained control above is the minus.
- **FastStream**: a nice declarative layer over aio-pika; a reasonable swap later. It adds
  a framework where this project wants the semantics in plain sight.
- **Dramatiq / arq**: simpler than Celery, but the same mismatch on queue arguments.
