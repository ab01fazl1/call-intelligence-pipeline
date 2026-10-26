# Benchmarks

Produced by `scripts/loadtest.py` against the local compose stack (mock ASR; see the label for MOCK_ASR_RTF).

| when (UTC) | label | ASR workers | calls | audio min | wall | calls/min | audio min/min | upload p95 | e2e p50 | e2e p95 | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-26 14:59 | mock rtf=0.05 | 2 | 100 | 96 | 274s | 21.9 | 20.9 | 0.45s | 136s | 259s | 0 |
| 2026-10-26 15:01 | mock rtf=0.05 | 4 | 100 | 96 | 141s | 42.5 | 40.6 | 0.36s | 67s | 128s | 0 |
| 2026-10-26 15:03 | mock rtf=0.05 | 8 | 100 | 96 | 74s | 80.8 | 77.2 | 0.39s | 36s | 67s | 0 |

## Reading the numbers

- Each call is ~58 s of stereo audio; the mock ASR sleeps `duration × 0.05` per channel, so one
  ASR worker handles ~10 calls/min. Throughput scales **near-linearly with ASR replicas**
  (2 → 4 → 8 workers: 21.9 → 42.5 → 80.8 calls/min, 1.94× and 3.69×), because preprocessing,
  analysis and the outbox hop are not bottlenecks. That is the point of ADR 0001.
- Upload p95 stays ~0.4 s at 16 concurrent uploads regardless of backlog: ingestion is
  decoupled from processing.
- End-to-end latency here is queueing time: 100 calls are submitted at once.
- Real Whisper `small` (int8, 4 CPU threads in Docker on an M3) measured RTF ≈ 0.1 on these
  calls, i.e. about the same per-worker capacity as this mock setting, but each replica needs
  ~1 GB RAM and real CPU, so the replica count is bounded by the machine, not the design.
- Laptop numbers (Docker Desktop, 8 vCPU, 8 GB). They show scaling behaviour, not production
  capacity.
