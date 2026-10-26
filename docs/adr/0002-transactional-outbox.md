# 0002: Transactional outbox for every publish

Status: accepted · 2026-10-26

## Context
The gateway must (1) insert the call row and (2) publish "preprocess this call". Workers must
(1) commit their results and (2) publish the next stage. These are two systems with no shared
transaction. Doing them one after the other has two failure windows:

- commit, then crash before publish: the call exists but no job does; it is stuck forever.
- publish, then the commit fails: a worker processes a call that does not exist.

Both are rare per request and certain at volume.

## Decision
No pipeline code publishes to RabbitMQ directly. It inserts a row into `outbox` **in the same
transaction** as the state change. A separate relay process (`callintel.relay`):

1. is woken by `LISTEN outbox` (a statement-level trigger runs `pg_notify` on insert) and
   also polls every second as a safety net;
2. claims rows with `SELECT ... FOR UPDATE SKIP LOCKED` (several relays can run safely);
3. publishes with **publisher confirms** and marks a row published only after the broker
   confirms it;
4. backs off failed rows exponentially (`next_attempt_at`), and garbage-collects published
   rows after 24 h.

## Consequences
+ A job can never be lost or orphaned: it exists if and only if the state change committed.
+ The gateway accepts uploads while RabbitMQ is down; the outbox is the buffer
  (chaos scenario `broker-outage`).
+ Publish failures become visible data (attempts, last_error) instead of log lines.
- The relay can publish a row twice (crash after confirm, before marking). Consumers must
  be idempotent; ADR 0003.
- One extra hop of latency (sub-50 ms with NOTIFY) and one more process to run.
- Ordering is per-batch best effort. That is fine because a call has at most one pipeline
  message in flight at a time.

## Alternatives considered
- **Publish after commit, accept the window**: the most common bug in queue-based systems.
- **Two-phase commit / XA**: not supported by RabbitMQ, and operationally painful anyway.
- **Change data capture (Debezium)**: the same pattern with Kafka Connect; too heavy here.
