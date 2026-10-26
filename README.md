# Call Intelligence Pipeline

An asynchronous backend that ingests call-center recordings, transcribes them in Persian, and
scores every call for QA and regulatory compliance, with evidence for every finding.

Brokerage call centers record every call, but human QA can audit only 1-2% of them. This
pipeline is built to audit 100%, and to stay correct when workers die, the broker restarts or
a corrupt file arrives.

```
             streamed upload            ┌──────────────┐
  PBX / API ───────────────▶ gateway ──▶│ object store │ raw + normalized audio
                              │         └──────────────┘
                              │ one transaction: call row + outbox row
                              ▼
                          PostgreSQL ──LISTEN/NOTIFY──▶ outbox relay ──confirms──▶ RabbitMQ
                              ▲                                                     │
                              │  claim · run · complete (CAS + next stage → outbox) │
                              └────────── workers: preprocess → asr → analysis ◀────┘
                                          retry queues 5s/30s/120s · delivery limit
                                          dead-letter fan-out → parking lot (+ replay)
```

## Results (local stack, measured)

| What | Result | Evidence |
|---|---|---|
| Fault injection | **12/12 scenarios pass**: SIGKILL mid-transcription, graceful drain, poison message, broker / relay / storage outages, retries exhausted → replay, duplicate delivery and upload | [chaos-results.md](docs/chaos-results.md), [failure-modes.md](docs/failure-modes.md) |
| Scaling | Throughput near-linear in ASR replicas: 2 → 4 → 8 workers = 21.9 → 42.5 → 80.8 calls/min, 0 failures | [benchmarks.md](docs/benchmarks.md) |
| Rules, perfect transcripts | v3: precision and recall 1.00 on every flag (upper bound by construction) | [eval.md](docs/eval.md) |
| Rules vs. ASR noise | Recall decays gracefully to ~10% WER; "required phrase" rules lose precision first | [rule-robustness.md](docs/rule-robustness.md) |
| Real ASR | faster-whisper `small` int8: RTF ≈ 0.1 on CPU, but **WER 0.76** on 8 kHz G.711 Persian. The pipeline is ready; the model is the bottleneck | [eval.md](docs/eval.md) |
| ML lifecycle | v3 (approximate matching) was built, re-scored on the same calls and promoted *because* the harness showed it beat v2 on Whisper transcripts (identity-check FPs 7 → 3, promise recall 0 → 0.25) with no loss on clean ones | [ADR 0011](docs/adr/0011-versioned-analysis.md) |

Bugs that building this surfaced, kept in the docs on purpose: a blind parking-lot replay
re-injecting a poison message, QA results keyed too narrowly to survive a new ASR model,
re-analysis skipped after re-transcription, a failed reprocess hiding valid results, a
faster-whisper/PyAV incompatibility, MP3 decoder glitches at read boundaries, and a chaos
test whose `docker compose start` silently revived the service it had just stopped.

## What makes it production-minded

| Concern | Approach | Details |
|---|---|---|
| A job is never lost or orphaned | Transactional outbox for **every** publish; relay with publisher confirms | [ADR 0002](docs/adr/0002-transactional-outbox.md) |
| Duplicates are harmless | Ack after commit; idempotency key (call, stage, version); compare-and-set completion; SHA-256 upload dedupe | [ADR 0003](docs/adr/0003-idempotent-stages.md) |
| Failures end somewhere visible | Typed errors → retry tiers or parking lot; quorum delivery limit stops poison messages; replay API | [ADR 0004](docs/adr/0004-retries-and-parking-lot.md) |
| Heavy audio, constant memory | Raw-body streaming into S3 multipart, size/type checks on the fly; streaming resampler | [ADR 0006](docs/adr/0006-streaming-uploads.md) |
| Long inference doesn't break the broker | Whisper in a separate process; heartbeats stay alive; per-call timeouts | [ADR 0007](docs/adr/0007-inference-off-the-event-loop.md) |
| Bursts degrade gracefully | Bounded queues → outbox buffer → 503 + Retry-After | [ADR 0012](docs/adr/0012-backpressure.md) |
| ML lifecycle | Versioned transcripts and QA results; re-score without re-transcribing; v1 vs. v2 evaluation | [ADR 0011](docs/adr/0011-versioned-analysis.md) |
| It is measured, not asserted | Synthetic Persian calls with ground truth; WER and per-rule precision/recall; chaos suite for the failure table | [ADR 0010](docs/adr/0010-pluggable-asr-synthetic-data.md), [failure modes](docs/failure-modes.md) |

All design decisions: [docs/adr](docs/adr/README.md).

## QA and compliance rules

Each rule is a detector with a severity, a weight and evidence (speaker, timestamps, text,
matched phrase). Rules that measure the **agent** lower the score; customer **insights** don't.

| Flag | Kind | v1 | v2 | v3 (default) |
|---|---|---|---|---|
| `prohibited_promise`: agent promises returns («سود تضمینی», «ضرر نمی‌کنید») | critical | ✓ | ✓ | ✓ |
| `investment_advice`: agent recommends buy/sell («حتما بفروشید») | critical | | ✓ | ✓ |
| `missing_identity_verification`: no national ID / account check | major | | ✓ | ✓ |
| `unannounced_hold`: ≥30 s of dead air (v2+ honour "please hold") | major | naive | ✓ | ✓ |
| `missing_greeting` / `missing_closing` | minor | ✓ | ✓ | ✓ |
| `excessive_interruptions`: ≥3 overlaps (stereo only) | minor | | ✓ | ✓ |
| `negative_sentiment`, `churn_risk`, `escalation_request` | insight | partly | ✓ | ✓ |
| Phrase matching | | exact | exact | approximate (ASR misspellings) |

Speaker attribution comes from PBX stereo channels (agent left, customer right): exact
diarization at no cost ([ADR 0008](docs/adr/0008-stereo-channels.md)). Persian text is
normalized (ي/ی, ك/ک, ZWNJ, digits) before matching ([ADR 0009](docs/adr/0009-persian-normalization.md)).

## Quick start

Needs Docker and [uv](https://docs.astral.sh/uv/). Mirrors for constrained networks are on
by default in `.env.example`; empty them to use upstream registries.

```bash
make env          # .env with registry / PyPI / HF mirrors
make install      # local venv for tests, data generation and scripts
make test         # unit tests, no infrastructure
make data         # 30 Persian calls via Edge TTS (+ edge cases); or: make data-offline
make up           # build and start the stack
make demo         # upload everything; open http://localhost:8000
```

Then:

```bash
make chaos        # fault-injection suite → docs/chaos-results.md
make eval         # WER + per-rule precision/recall → docs/eval.md
make robustness   # how each rule decays with ASR word errors → docs/rule-robustness.md (no stack)
make scale N=4    # more ASR workers
make up-obs       # + Prometheus and Grafana on http://localhost:3000
make data-load && make bench   # throughput → docs/benchmarks.md
make whisper      # swap the mock for faster-whisper `small` (big download, see below)
```

### Download budget (first run)

| What | Size | When |
|---|---|---|
| Base images: python-slim, postgres, rabbitmq, versitygw | ~280 MB | `make up` |
| Python wheels inside the image (cached across rebuilds) | ~60 MB | `make up` |
| Local venv | ~130 MB on disk, ~45 MB download | `make install` |
| Edge TTS audio for the dataset (cached) | ~5 MB | `make data` |
| Prometheus + Grafana | ~300 MB | `make up-obs` only |
| faster-whisper deps + `small` model | ~70 MB + ~480 MB | `make whisper` only |

If the registry mirror returns a transient error (it happened once with a 502), pull that image
directly and retag it, e.g. `docker pull versity/versitygw && docker tag versity/versitygw
docker.arvancloud.ir/versity/versitygw`. Images install exact, hash-verified dependency
versions from `requirements/*.txt`, exported from `uv.lock` by `make lock`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/calls?external_id=` | Upload audio as the raw body (`audio/wav`, `audio/mpeg`) → `202` (or `200` duplicate) |
| `GET` | `/v1/calls?status=&flag=&external_prefix=` | List calls with the latest QA result |
| `GET` | `/v1/calls/{id}` | Stages and attempts, transcript, QA results for every version |
| `GET` | `/v1/search?q=` | Persian-normalized transcript search (pg_trgm) |
| `GET` | `/v1/analytics/summary?version=` | Status counts, flag rates, scores, end-to-end p50/p95, outbox backlog |
| `POST` | `/v1/reprocess` | Re-run a stage (`analysis` re-scores, `asr` re-transcribes) |
| `POST` | `/v1/admin/parking-lot/replay` | Move parked messages back into the pipeline |
| `GET` | `/v1/events` | Server-sent events (live dashboard) |
| `GET` | `/readyz`, `/healthz`, `/metrics` | Readiness (DB + storage), liveness, Prometheus |

Interactive docs: http://localhost:8000/docs

## Repository layout

```
src/callintel/
  gateway/      FastAPI: streaming upload, queries, SSE, admin, status page
  relay/        outbox → RabbitMQ publisher
  workers/      StageWorker (delivery semantics), stages, dead-letter handler, retry policy
  messaging/    envelope schema, topology (quorum, retry, DLX)
  audio/        streaming resampler, VAD + conversation features, preprocessing
  asr/          backend interface, oracle mock, faster-whisper (process pool)
  analysis/     lexicons, rules, versioned rulesets and scoring
  text/         Persian normalization and phrase matching
  synth/        synthetic call generator (scripts, TTS/offline voices, phone-line simulation)
  eval/         WER and precision/recall against ground truth
migrations/     Alembic (schema, indexes, outbox NOTIFY trigger)
scripts/        upload, chaos suite, load test
infra/          RabbitMQ, Prometheus, Grafana (dashboard as code)
docs/           ADRs, failure modes, demo script, generated eval/benchmark/chaos reports
```

## Honest limitations

- **The data is synthetic.** TTS speech is cleaner than real phone calls, so ASR word error
  rate on it overstates real accuracy. The mock ASR returns ground truth, so it tests the
  system, not a model. Rule scores measure rule logic on clean transcripts. The next step
  with real data: a few hundred human-labelled calls through the same `make eval`.
- **Whisper `small` on CPU** is fast enough for a demo, but its Persian is rough. Production
  would use a Persian fine-tuned checkpoint (converted to CTranslate2) on GPU.
- **Phrase lexicons miss paraphrases.** That is where a classifier or LLM judge should be
  added as another versioned detector ([ADR 0014](docs/adr/0014-rules-before-models.md)).
- **Single-node RabbitMQ.** The quorum-queue semantics are exercised; their availability is not.
- No authentication or multi-tenancy, and no PII redaction: callers read out national ID
  numbers, so transcripts are sensitive data.

## Production path

- Presigned uploads directly to S3; bucket lifecycle rules for raw audio retention.
- Kubernetes: gateway HPA on CPU; workers autoscaled by KEDA on queue depth; ASR on a GPU
  node pool; three-node RabbitMQ with policies managed as code.
- PII redaction stage (national IDs, card numbers) before transcripts are stored or searched;
  encryption at rest; audit log of who listened to what.
- OpenTelemetry tracing (the `trace_id` already flows through every message and log line).
- Alerts: parking lot non-empty, outbox backlog growth, ASR queue age, p95 end-to-end time.

## License

MIT, see [LICENSE](LICENSE).
