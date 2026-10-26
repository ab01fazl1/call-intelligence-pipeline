# Demo script

## Before recording

```bash
make up-obs                    # pipeline + Grafana (or `make up` without it)
make scale N=2                 # two ASR workers to start with
```

Arrange three windows: the dashboard at http://localhost:8000, Grafana at http://localhost:3000
(dashboard "Call Intelligence Pipeline"), and a terminal. Keep `make logs` in a second tab in
case something needs explaining.

## The 45-second version (Loom)

| t | Show | Say (roughly) |
|---|---|---|
| 0-6 s | Dashboard, empty | "Call-center QA pipeline: Persian ASR and compliance scoring for 100% of calls, not the 1-2% humans can audit." |
| 6-14 s | `make demo`; rows stream in, Grafana queue depth rises | "Uploads stream straight to object storage: constant memory, deduplicated by hash. Each stage has its own queue." |
| 14-22 s | `make scale N=6`; ASR queue drains faster | "ASR is the bottleneck, so only ASR scales." |
| 22-32 s | `docker compose kill -s SIGKILL worker-asr` during processing, then `make scale N=6` | "I kill every transcription worker mid-call. Nothing acked, nothing lost: messages are redelivered and results commit exactly once." |
| 32-38 s | Click the failed `edge_corrupt` and `edge_poison_crash` rows | "A corrupt file fails once and is parked with its reason. A file that crashes the worker is stopped by the broker's delivery limit." |
| 38-45 s | Click a violation call: RTL transcript, `prohibited_promise` evidence, v1/v2 rows | "Every flag has evidence: who said it, and when. Rules are versioned, so re-scoring never rewrites history." |

## The 3-minute version (interview, live)

1. **Architecture** (30 s): README diagram. Name the outbox and why it exists.
2. **Ingestion** (20 s): `curl --data-binary @data/synthetic/calls/call_0001.wav -H 'content-type: audio/wav' localhost:8000/v1/calls`
   twice: 202, then 200 `duplicate: true`.
3. **Chaos** (60 s): `uv run python scripts/chaos.py kill-asr-worker broker-outage`.
   While it runs, open `docs/failure-modes.md` and point at the rows being proven.
4. **ML lifecycle** (40 s): `make reprocess-v1`, wait, `make eval`. Show v1 vs. v2: v2 catches
   investment advice and removes v1's hold false positives. Mention that WER on synthetic
   TTS overstates real accuracy.
5. **What's next** (30 s): presigned uploads, KEDA autoscaling on queue depth, GPU node pool,
   PII redaction (national IDs are spoken in calls), labelled real-call evaluation set.

## Likely questions, and where the answer lives

| Question | Answer in |
|---|---|
| Why not Celery? | ADR 0005 |
| What if the worker dies after committing but before acking? | ADR 0003: redelivery → claim sees `succeeded` → ack and skip |
| What if the relay dies after publishing but before marking? | ADR 0002: republished, consumers idempotent |
| Why does a blocking model call break RabbitMQ? | ADR 0007: heartbeats |
| How do you know the rules work? | ADR 0010 + `docs/eval.md` |
| What happens on a burst? | ADR 0012 |
| How would this run in production? | README, "Production path" |
