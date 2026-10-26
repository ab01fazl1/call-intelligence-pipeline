# 0014: Rule-based QA first, models as additional detectors

Status: accepted · 2026-10-26

## Context
"Use an LLM to score the call" is tempting. For a regulated brokerage, a compliance finding
must be **explainable** ("which sentence, said by whom, at what second"), **deterministic**
(the same call gets the same verdict next month) and **cheap** enough to run on 100% of calls,
not the 1-2% humans can audit.

## Decision
- QA is a set of small detector rules, each with a flag, severity and weight, grouped into
  versioned rulesets (ADR 0011). Every finding carries **evidence**: speaker, timestamps,
  utterance and matched phrase.
- Agent-behaviour rules lower the score (missing greeting or identity check, prohibited
  promise of returns, personalised investment advice, unannounced hold, interruptions).
  Customer-insight detectors (negative sentiment, churn risk, escalation request) are reported
  with weight 0. Customer anger must not lower an agent's score.
- Lexicons are data (`analysis/lexicons.py`), so compliance staff can own them.
- Any critical finding sets `compliance = "violation"` regardless of score.
- Matching is exact (v2) or approximate (v3: Sellers' approximate-substring search, about
  one character edit per five, tolerating ASR misspellings). Both stay deterministic and
  explainable: the evidence shows the transcribed text that matched.

## Consequences
+ Auditable output: a supervisor can verify every flag in seconds.
+ Precision and recall are measurable per rule (`docs/eval.md`).
- Phrase lexicons miss paraphrases ("this stock can only go up"). That gap is where a model
  earns its place: a classifier (e.g. ParsBERT fine-tuned on labelled calls) or an LLM judge
  added **as another detector** behind the same `Rule` interface, with its own version and
  measured on the same harness, rather than replacing the explainable layer.
