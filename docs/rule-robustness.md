# Rule robustness

Generated 2026-10-26 11:32 UTC by `scripts/offline_eval.py` over 31 synthetic calls. Audio features come from the real preprocessing; transcripts are ground truth with injected word errors (deletion / substitution / insertion), so this measures the rules, not an ASR model.

## Analyzer v1: recall

| flag | positives | recall @ WER 0.00 | recall @ WER 0.09 | recall @ WER 0.18 | recall @ WER 0.29 |
|---|---|---|---|---|---|
| escalation_request | 2 | 1.00 | 1.00 | 1.00 | 1.00 |
| missing_closing | 5 | 1.00 | 1.00 | 1.00 | 1.00 |
| missing_greeting | 4 | 1.00 | 1.00 | 1.00 | 1.00 |
| negative_sentiment | 6 | 1.00 | 0.83 | 1.00 | 0.17 |
| prohibited_promise | 10 | 1.00 | 1.00 | 0.90 | 0.40 |
| unannounced_hold | 10 | 1.00 | 1.00 | 1.00 | 1.00 |

## Analyzer v2: recall

| flag | positives | recall @ WER 0.00 | recall @ WER 0.09 | recall @ WER 0.18 | recall @ WER 0.29 |
|---|---|---|---|---|---|
| churn_risk | 3 | 1.00 | 1.00 | 0.67 | 0.67 |
| escalation_request | 2 | 1.00 | 1.00 | 1.00 | 1.00 |
| excessive_interruptions | 4 | 1.00 | 1.00 | 1.00 | 1.00 |
| investment_advice | 1 | 1.00 | 1.00 | 1.00 | 0.00 |
| missing_closing | 5 | 1.00 | 1.00 | 1.00 | 1.00 |
| missing_greeting | 4 | 1.00 | 1.00 | 1.00 | 1.00 |
| missing_identity_verification | 6 | 1.00 | 1.00 | 1.00 | 1.00 |
| negative_sentiment | 6 | 1.00 | 0.83 | 1.00 | 0.17 |
| prohibited_promise | 10 | 1.00 | 1.00 | 0.90 | 0.40 |
| unannounced_hold | 10 | 1.00 | 1.00 | 1.00 | 1.00 |

## Analyzer v2: precision

| flag | precision @ WER 0.00 | precision @ WER 0.09 | precision @ WER 0.18 | precision @ WER 0.29 |
|---|---|---|---|---|
| churn_risk | 1.00 | 1.00 | 1.00 | 1.00 |
| escalation_request | 1.00 | 1.00 | 1.00 | 1.00 |
| excessive_interruptions | 1.00 | 1.00 | 1.00 | 1.00 |
| investment_advice | 1.00 | 1.00 | 1.00 | – |
| missing_closing | 1.00 | 1.00 | 1.00 | 1.00 |
| missing_greeting | 1.00 | 1.00 | 0.67 | 0.67 |
| missing_identity_verification | 1.00 | 0.86 | 0.55 | 0.55 |
| negative_sentiment | 1.00 | 1.00 | 1.00 | 1.00 |
| prohibited_promise | 1.00 | 1.00 | 1.00 | 1.00 |
| unannounced_hold | 1.00 | 1.00 | 1.00 | 0.91 |

Recall at WER 0 is an upper bound by construction: the generator and the rules share the phrase inventory. The slope as WER grows is the informative part: phrase rules fail when any word of the phrase is misrecognized, which is the case for adding a model-based detector next to them (ADR 0014).
