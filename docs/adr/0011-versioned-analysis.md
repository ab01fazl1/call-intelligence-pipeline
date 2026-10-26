# 0011: Versioned analysis results and reprocessing (ML lifecycle)

Status: accepted · 2026-10-26

## Context
QA rules and models change: a new compliance rule is added, a threshold is tuned, the ASR model
is upgraded. If results are overwritten in place:
- historical reports change silently ("why was this agent's March score 92 yesterday and 78 today?");
- there is no way to compare old and new behaviour on the same calls before switching.

## Decision
- Every result row records the producer's version: `transcripts.model`
  (`faster-whisper/small-int8`, `mock-oracle/wer0`) and `qa_results.analyzer_version` (`v1`,
  `v2`, `v3`) **plus the transcript model it scored**. Unique constraints are
  `(call_id, model)` and `(call_id, analyzer_version, transcript_model)`.
  (The first schema keyed QA results on `(call_id, analyzer_version)` only; re-transcribing
  with Whisper would have overwritten the mock-transcript scores. Migration 0002 fixed it.)
- A stage whose committed output changes the next stage's input **invalidates** it:
  a new transcript enqueues analysis with `force=true`, because "v2 already succeeded" was
  computed on a different transcript. The forced message is enqueued once per committed
  result (after the compare-and-set), so it cannot amplify duplicates.
- A failed reprocess or replay of a call that had completed leaves it `completed`; the failure
  is recorded on the stage run and as a `reprocess.failed` event. Old results stay valid.
- A ruleset version is immutable code (`analysis/scoring.py: RULESETS`). Changing rules means
  adding a version.
- The idempotency key includes the version (ADR 0003), so a worker running `v2` treats
  "analysis done by v1" as not done.
- `POST /v1/reprocess {"stage": "analysis", "status": "completed"}` enqueues re-scoring of
  existing calls through the outbox, reusing their transcripts (ASR is not repeated). With
  `"stage": "asr"` it re-transcribes and cascades to analysis.
- APIs prefer the configured `ANALYZER_VERSION` and still expose all versions per call, so a
  rollout is: deploy v2 workers, reprocess, compare with `make eval`, switch the default.

## Consequences
+ Shadow-scoring and backfills are a first-class operation, not a migration script.
+ `docs/eval.md` shows v1 against v2 on the same calls. For example, v2's hold rule honours
  "please hold" announcements and removes v1's false positives.
+ The loop was used for real: Whisper transcripts showed v2's exact phrase matching
  misreading misspellings («کود ملی» for «کد ملی») as missing identity checks. v3 adds
  approximate matching; on the same calls it cut those false positives from 7 to 3 and raised
  `prohibited_promise` recall from 0 to 0.25, with no loss on clean transcripts.
  v3 was promoted to the default on that evidence.
- Storage grows with versions. Old versions can be pruned by policy once retired.
- Rules are code-versioned, not data-versioned. A model registry (MLflow) becomes useful
  once trained models replace rules (ADR 0014).
