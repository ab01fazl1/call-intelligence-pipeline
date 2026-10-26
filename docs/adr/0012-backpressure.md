# 0012: End-to-end backpressure

Status: accepted · 2026-10-26

## Context
Calls arrive in bursts: market open, a trading halt, an IPO day. ASR capacity is fixed in the
short term. An unbounded pipeline converts a burst into broker memory pressure (RabbitMQ
blocks *all* publishers at its memory watermark) and then into an outage.

## Decision
Push the pressure back toward the edge in stages:

1. **Workers**: prefetch bounds in-flight work per process (ASR: 1).
2. **Queues**: stage queues have `x-max-length` with `x-overflow: reject-publish`. When a
   queue is full, the broker *nacks* new publishes instead of buffering them in RAM.
3. **Outbox**: the relay treats a nack as a failed publish. The row stays in Postgres with
   exponential backoff. Postgres, on disk, becomes the buffer.
4. **Gateway**: when unpublished outbox rows exceed `OUTBOX_BACKLOG_LIMIT`, uploads (and
   reprocess requests) get **503 with Retry-After**. The PBX export job retries later, and
   nothing is lost because the recording is still on the PBX.

Retry queues use the at-least-once dead-letter strategy, so a retry whose target queue is
full waits instead of being dropped.

## Consequences
+ Overload degrades into latency first and explicit rejections second, never into data loss
  or a broker meltdown.
+ Backlog is a single number to alert on, and to autoscale on (Grafana "Outbox backlog",
  queue depth).
- The limits need tuning per deployment. Defaults: 10 000 messages per queue, 5 000 pending
  outbox rows.
- The gateway's backlog check is a cached `count(*)` over a partial index (1 s TTL), not an
  exact real-time limit.
