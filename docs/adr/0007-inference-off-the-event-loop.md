# 0007: ASR inference runs in a separate process

Status: accepted · 2026-10-26

## Context
faster-whisper (CTranslate2) inference is CPU-bound native code that runs for seconds to
minutes. If it runs on the asyncio event loop:

1. the loop cannot answer **AMQP heartbeats**;
2. RabbitMQ declares the connection dead (default heartbeat 60 s) and closes it;
3. the unacked message is **redelivered** to another worker, which also blocks, and so on.

The result is a redelivery storm where every call is transcribed several times and none
completes. It is one of the most common production failures in Python ML pipelines, and it
looks like "RabbitMQ is flaky".

Separately, a native crash (segfault, OOM kill) inside inference must not corrupt the
consumer's state.

## Decision
- The Whisper model lives in a dedicated child process (`ProcessPoolExecutor(max_workers=1)`,
  `spawn` context), loaded once by the pool initializer and warmed up before consuming starts.
- The event loop only awaits a future, so heartbeats, metrics and shutdown signals keep working.
- A per-call timeout scales with audio length: `max(60 s, duration × channels × rtf_budget)`.
  On timeout the child process is terminated and the pool rebuilt (a stuck native call cannot
  be cancelled any other way), and the message goes through the retry policy (ADR 0004).
- `BrokenProcessPool` (the child died) becomes a `TransientError`, and the pool is rebuilt.
- ASR workers use prefetch 1: one call per process, scale with replicas.
- Preprocessing (numpy, which releases the GIL) uses a thread, which is enough to keep the
  loop responsive.
- RabbitMQ's `consumer_timeout` (1 h) is set above the largest ASR timeout, so the worker's
  own timeout always fires first and is handled cleanly.

## Consequences
+ Long transcriptions never cost a connection (chaos: `kill-asr-worker` and
  `graceful-shutdown` both rely on the loop staying responsive during ASR).
- Process start plus model load add about 10 s of warm-up per worker start.
- The model is held once per worker process; memory sizing is per replica.
