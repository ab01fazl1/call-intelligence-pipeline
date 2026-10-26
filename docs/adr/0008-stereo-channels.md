# 0008: Use stereo channels as speaker separation; normalize to 16 kHz mono per channel

Status: accepted · 2026-10-26

## Context
QA rules are about *who* said something: an agent promising returns is a violation, a
customer asking about returns is not. Speaker diarization models are expensive and error-prone
on 8 kHz phone audio. But PBX systems (Asterisk, Issabel, Cisco) commonly record each leg of
the call on its own channel: agent left, customer right.

Whisper and most ASR models expect 16 kHz mono.

## Decision
- Preprocessing splits stereo recordings into one 16 kHz mono file per channel, and ASR runs
  per channel. Each segment's speaker comes from a configurable `CHANNEL_MAP`
  (default `agent,customer`). That is perfect diarization at zero cost.
- Mono recordings are supported, but every utterance is labelled `unknown`. Rules then consider
  all speech for both parties and mark their findings `low_confidence`. Interruption detection
  is reported as *not applicable* rather than guessed.
- Resampling 8 kHz to 16 kHz is a **format conversion only**: it adds no information above
  4 kHz. The resampler is a general streaming polyphase FIR (Kaiser-windowed sinc, like
  `scipy.signal.resample_poly`), because MP3 sources may arrive at 44.1/48 kHz and need proper
  anti-aliasing. Streaming keeps memory constant for multi-hour recordings.
- Energy-based VAD per channel produces ASR-independent features: talk ratio, internal
  silences (holds), and overlap events (both channels speaking, i.e. interruptions).

## Consequences
+ Speaker attribution is exact on stereo. The evaluation shows the cost of mono: the greeting
  rule's errors there come from the customer's «سلام» being indistinguishable from the agent's.
+ Hold and interruption detection work even with a mock or failed ASR.
- Real recordings have echo bleed between channels, so the VAD would need per-channel
  thresholds or echo-aware gating. Synthetic data does not model bleed.
- Splitting doubles ASR invocations, but each is half the audio, so total work is unchanged.
