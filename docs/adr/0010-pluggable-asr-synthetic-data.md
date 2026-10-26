# 0010: Pluggable ASR backends and synthetic calls with ground truth

Status: accepted · 2026-10-26

## Context
Real call recordings are customer data protected by regulation and are not available for a
prototype. Without data, the usual demo is "it runs". A senior engineer wants "it is correct
and here is how I know". ASR quality, rule quality and system behaviour need to be measured
separately.

## Decision
- **ASR is an interface** (`asr/base.py`) with two backends:
  - `WhisperASR`: faster-whisper with language `fa`, int8 on CPU (ADR 0007);
  - `MockASR`: an *oracle* that returns the ground-truth transcript of a synthetic call, found
    by the upload's SHA-256. It sleeps proportionally to the audio length (`MOCK_ASR_RTF`), can
    inject word errors (`MOCK_ASR_WER`) and transient failures (`MOCK_ASR_FAILURE_RATE`), and
    can be told by a ground-truth file to **crash the process**, which simulates a native
    segfault for poison-message tests.
- **Synthetic data generator** (`callintel.synth`):
  - call scripts are assembled from labelled blocks: greeting or not, identity verification,
    six support topics, violations, angry/churning/escalating customers, announced or
    unannounced holds, interruptions;
  - expected QA flags are known by construction;
  - voices: Microsoft Edge neural TTS in Persian (cached), or a fully offline speech-like
    synthesizer for load tests;
  - telephone line simulation: 300-3400 Hz band-pass, line hiss, mains hum, G.711 μ-law;
  - PBX-like formats: 8 kHz PCM16 stereo, 8 kHz μ-law stereo, 16 kHz MP3 mono;
  - edge cases: corrupt codec, too short, not audio, empty, truncated, a 6-topic long call,
    and a poison file.
- **Evaluation harness** (`callintel.eval`): WER per ASR model and per-rule
  precision/recall/F1 per analyzer version, written to `docs/eval.md`.

## Consequences
+ The whole system (throughput, chaos, scoring) is testable on a laptop with no customer data.
+ Rule changes are judged by numbers (v1 vs. v2 in `docs/eval.md`), not anecdotes.
- TTS audio is cleaner and more regular than real calls: WER on it **overstates** real-world
  accuracy, and the rule scores measure rule logic on clean transcripts, not field accuracy.
  The next step with real data would be a few hundred human-labelled calls.
- The oracle can only "transcribe" files it generated. Unknown audio gets an empty transcript
  with a warning, by design.
