# 0001: Staged pipeline with one queue per stage

Status: accepted · 2026-10-26

## Context
Processing a call has three steps with very different resource profiles:

| stage | cost per call | bound by |
|---|---|---|
| preprocess (decode, resample, VAD) | ~0.1-0.5 s | CPU, briefly |
| ASR | seconds to minutes (real-time factor 0.1-1.0 on CPU) | CPU/GPU, memory for the model |
| analysis (rules) | milliseconds | nothing |

A single "process call" task would force every worker to hold an ASR model, scale everything
together, and repeat the whole pipeline when only the last step needs re-running.

## Decision
Each stage is its own worker type consuming its own quorum queue (`callintel.preprocess`,
`callintel.asr`, `callintel.analysis`) on a direct exchange. A stage hands off to the next by
writing the next stage's message to the outbox in its completion transaction (ADR 0002).
Messages carry identifiers only; audio lives in object storage (ADR 0006).

## Consequences
+ ASR workers scale independently (`docker compose up --scale worker-asr=8`, or KEDA on
  queue depth in Kubernetes), and can run on GPU nodes while the rest stays on cheap CPU.
+ Any stage can be re-run alone (`POST /v1/reprocess {"stage": "analysis"}`).
+ Queue depth per stage is a direct bottleneck signal (Grafana "Queue depth").
- More moving parts and an intermediate artifact (normalized audio) per call.
- Per-call latency includes hand-off overhead (one outbox hop, typically < 50 ms with NOTIFY).

## Alternatives considered
- **One task per call**: simpler, but couples scaling and prevents partial reprocessing.
- **Workflow engine (Temporal, Airflow)**: real benefits for long multi-step workflows,
  but a heavy dependency for a three-step linear pipeline. Worth revisiting if the pipeline
  grows branches (e.g. human review loops).
