from dataclasses import dataclass, field
from typing import Any

AGENT = "agent"
CUSTOMER = "customer"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Utterance:
    speaker: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class CallView:
    """Everything the rules may look at: transcript + ASR-independent audio features."""

    duration_s: float
    utterances: tuple[Utterance, ...]
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def stereo(self) -> bool:
        return any(u.speaker != UNKNOWN for u in self.utterances)

    def by(self, speaker: str) -> list[Utterance]:
        """Utterances of one party. In mono recordings the speaker is unknown, so every
        utterance is a candidate (lower precision, reported as low confidence)."""
        return [u for u in self.utterances if u.speaker in (speaker, UNKNOWN)]

    @classmethod
    def from_segments(cls, duration_s: float, segments: list[dict[str, Any]],
                      features: dict[str, Any] | None = None) -> "CallView":
        utterances = tuple(
            Utterance(str(s.get("speaker", UNKNOWN)), float(s["start"]), float(s["end"]),
                      str(s["text"]))
            for s in sorted(segments, key=lambda s: s["start"])
            if str(s.get("text", "")).strip()
        )
        return cls(duration_s=duration_s, utterances=utterances, features=features or {})
