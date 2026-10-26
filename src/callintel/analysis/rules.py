"""QA and compliance rules.

Every rule is a detector that either raises its flag or not. Rules with a weight measure the
*agent* and lower the QA score; weight-0 rules are customer insights (sentiment, churn,
escalation) that are reported but never penalise the agent.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from callintel.analysis import lexicons as lx
from callintel.analysis.model import AGENT, CUSTOMER, CallView, Utterance
from callintel.text.normalize import Lexicon


class Severity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Evidence:
    speaker: str
    start: float
    end: float
    text: str
    matched: list[str]


@dataclass(frozen=True)
class Finding:
    flag: str
    triggered: bool
    severity: Severity
    weight: int
    applicable: bool = True
    low_confidence: bool = False
    note: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Rule(Protocol):
    flag: str
    severity: Severity
    weight: int

    def evaluate(self, call: CallView) -> Finding: ...


def _hits(utterances: list[Utterance], lexicon: Lexicon) -> list[Evidence]:
    found = []
    for u in utterances:
        if matched := lexicon.find(u.text):
            found.append(Evidence(u.speaker, u.start, u.end, u.text, matched))
    return found


@dataclass(frozen=True)
class _Base:
    flag: str
    severity: Severity
    weight: int

    def _finding(self, call: CallView, triggered: bool, *, evidence: list[Evidence] | None = None,
                 note: str = "", applicable: bool = True) -> Finding:
        return Finding(self.flag, triggered, self.severity, self.weight, applicable=applicable,
                       low_confidence=not call.stereo, note=note, evidence=evidence or [])


@dataclass(frozen=True)
class RequiredPhrase(_Base):
    """Agent must say something from `lexicon` inside a time window."""

    lexicon: Lexicon
    window: str  # "start" | "end" | "first_half"
    seconds: float = 15.0

    def evaluate(self, call: CallView) -> Finding:
        def in_window(u: Utterance) -> bool:
            if self.window == "start":
                return u.start <= self.seconds
            if self.window == "end":
                return u.end >= call.duration_s - self.seconds
            return u.start <= call.duration_s * 0.6

        evidence = _hits([u for u in call.by(AGENT) if in_window(u)], self.lexicon)
        return self._finding(call, not evidence, evidence=evidence)


@dataclass(frozen=True)
class ForbiddenPhrase(_Base):
    """Raised when `speaker` says anything from `lexicon`."""

    lexicon: Lexicon
    speaker: str = AGENT
    min_hits: int = 1

    def evaluate(self, call: CallView) -> Finding:
        evidence = _hits(call.by(self.speaker), self.lexicon)
        hits = sum(len(e.matched) for e in evidence)
        return self._finding(call, hits >= self.min_hits, evidence=evidence)


@dataclass(frozen=True)
class UnannouncedHold(_Base):
    """Long dead air. v2 forgives a hold the agent announced just before it started."""

    min_silence_s: float = 30.0
    announce_window_s: float = 15.0
    honour_announcements: bool = True

    def evaluate(self, call: CallView) -> Finding:
        holds = [s for s in call.features.get("silences", [])
                 if s["duration_s"] >= self.min_silence_s]
        unannounced, evidence = [], []
        for hold in holds:
            notices = [
                e for e in _hits(call.by(AGENT), lx.HOLD_NOTICE)
                if hold["start_s"] - self.announce_window_s <= e.end <= hold["start_s"] + 1.0
            ]
            if self.honour_announcements and notices:
                continue
            unannounced.append(hold)
            evidence.append(Evidence("line", hold["start_s"], hold["end_s"],
                                     f"{hold['duration_s']:.0f}s of silence", []))
        return self._finding(call, bool(unannounced), evidence=evidence,
                             note=f"{len(holds)} hold(s) >= {self.min_silence_s:.0f}s")


@dataclass(frozen=True)
class ExcessiveInterruptions(_Base):
    min_events: int = 3

    def evaluate(self, call: CallView) -> Finding:
        if not call.stereo:
            return self._finding(call, False, applicable=False,
                                 note="needs one channel per speaker")
        events = call.features.get("overlap_events", [])
        evidence = [Evidence("both", e["start_s"], e["end_s"], "overlapping speech", [])
                    for e in events]
        return self._finding(call, len(events) >= self.min_events, evidence=evidence,
                             note=f"{len(events)} overlap(s)")


MISSING_GREETING = RequiredPhrase("missing_greeting", Severity.MINOR, 10,
                                  lexicon=lx.GREETING, window="start", seconds=15)
MISSING_CLOSING = RequiredPhrase("missing_closing", Severity.MINOR, 5,
                                 lexicon=lx.CLOSING, window="end", seconds=30)
MISSING_IDENTITY_CHECK = RequiredPhrase("missing_identity_verification", Severity.MAJOR, 15,
                                        lexicon=lx.IDENTITY_CHECK, window="first_half")
PROHIBITED_PROMISE = ForbiddenPhrase("prohibited_promise", Severity.CRITICAL, 40,
                                     lexicon=lx.PROHIBITED_PROMISE)
INVESTMENT_ADVICE = ForbiddenPhrase("investment_advice", Severity.CRITICAL, 30,
                                    lexicon=lx.INVESTMENT_ADVICE)
NEGATIVE_SENTIMENT = ForbiddenPhrase("negative_sentiment", Severity.INFO, 0,
                                     lexicon=lx.NEGATIVE, speaker=CUSTOMER, min_hits=2)
CHURN_RISK = ForbiddenPhrase("churn_risk", Severity.INFO, 0, lexicon=lx.CHURN, speaker=CUSTOMER)
ESCALATION_REQUEST = ForbiddenPhrase("escalation_request", Severity.INFO, 0,
                                     lexicon=lx.ESCALATION, speaker=CUSTOMER)
NAIVE_HOLD = UnannouncedHold("unannounced_hold", Severity.MAJOR, 10, honour_announcements=False)
HOLD = UnannouncedHold("unannounced_hold", Severity.MAJOR, 10)
INTERRUPTIONS = ExcessiveInterruptions("excessive_interruptions", Severity.MINOR, 5)
