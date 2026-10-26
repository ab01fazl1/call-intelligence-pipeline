# Evaluation

Generated 2026-10-26 14:52 UTC by `python -m callintel.eval` over 32 completed synthetic calls with ground truth.

## Transcription

| ASR model | calls | reference words | WER |
|---|---|---|---|
| faster-whisper/small-int8 | 10 | 1234 | 0.758 |
| mock-oracle/wer0 | 32 | 4109 | 0.000 |

The mock ASR returns the ground truth, optionally with injected word errors (`MOCK_ASR_WER`), so its WER measures the injection, not a model. Real models are only comparable on real or TTS audio decoded by the model itself.

## QA rules: analyzer v1 on mock-oracle/wer0 transcripts

| flag | TP | FP | FN | precision | recall | F1 | n/a |
|---|---|---|---|---|---|---|---|
| escalation_request | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_closing | 5 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_greeting | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| negative_sentiment | 6 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| prohibited_promise | 10 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| unannounced_hold | 10 | 2 | 0 | 0.83 | 1.00 | 0.91 | 0 |

## QA rules: analyzer v2 on faster-whisper/small-int8 transcripts

| flag | TP | FP | FN | precision | recall | F1 | n/a |
|---|---|---|---|---|---|---|---|
| churn_risk | 0 | 0 | 1 | – | 0.00 | – | 0 |
| escalation_request | 0 | 0 | 1 | – | 0.00 | – | 0 |
| excessive_interruptions | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 | 1 |
| investment_advice | 0 | 0 | 1 | – | 0.00 | – | 0 |
| missing_closing | 2 | 3 | 0 | 0.40 | 1.00 | 0.57 | 0 |
| missing_greeting | 0 | 1 | 0 | 0.00 | – | – | 0 |
| missing_identity_verification | 2 | 7 | 0 | 0.22 | 1.00 | 0.36 | 0 |
| negative_sentiment | 0 | 0 | 2 | – | 0.00 | – | 0 |
| prohibited_promise | 0 | 0 | 4 | – | 0.00 | – | 0 |
| unannounced_hold | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |

## QA rules: analyzer v2 on mock-oracle/wer0 transcripts

| flag | TP | FP | FN | precision | recall | F1 | n/a |
|---|---|---|---|---|---|---|---|
| churn_risk | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| escalation_request | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| excessive_interruptions | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 3 |
| investment_advice | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_closing | 5 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_greeting | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_identity_verification | 6 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| negative_sentiment | 6 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| prohibited_promise | 10 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| unannounced_hold | 10 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |

## QA rules: analyzer v3 on faster-whisper/small-int8 transcripts

| flag | TP | FP | FN | precision | recall | F1 | n/a |
|---|---|---|---|---|---|---|---|
| churn_risk | 0 | 0 | 1 | – | 0.00 | – | 0 |
| escalation_request | 0 | 0 | 1 | – | 0.00 | – | 0 |
| excessive_interruptions | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 | 1 |
| investment_advice | 0 | 0 | 1 | – | 0.00 | – | 0 |
| missing_closing | 2 | 3 | 0 | 0.40 | 1.00 | 0.57 | 0 |
| missing_greeting | 0 | 1 | 0 | 0.00 | – | – | 0 |
| missing_identity_verification | 2 | 3 | 0 | 0.40 | 1.00 | 0.57 | 0 |
| negative_sentiment | 0 | 0 | 2 | – | 0.00 | – | 0 |
| prohibited_promise | 1 | 0 | 3 | 1.00 | 0.25 | 0.40 | 0 |
| unannounced_hold | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |

## QA rules: analyzer v3 on mock-oracle/wer0 transcripts

| flag | TP | FP | FN | precision | recall | F1 | n/a |
|---|---|---|---|---|---|---|---|
| churn_risk | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| escalation_request | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| excessive_interruptions | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 | 2 |
| investment_advice | 0 | 0 | 0 | – | – | – | 0 |
| missing_closing | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_greeting | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| missing_identity_verification | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| negative_sentiment | 4 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| prohibited_promise | 6 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
| unannounced_hold | 6 | 0 | 0 | 1.00 | 1.00 | 1.00 | 0 |
