"""Versioned rulesets and the QA report.

A ruleset version is an immutable contract: results are stored per version, so changing
rules means adding a version and re-scoring (POST /v1/reprocess), never editing in place.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any

from callintel.analysis import lexicons as lx
from callintel.analysis import rules as r
from callintel.analysis.model import AGENT, CUSTOMER, CallView

RULESETS: dict[str, tuple[r.Rule, ...]] = {
    "v1": (
        r.MISSING_GREETING, r.MISSING_CLOSING, r.PROHIBITED_PROMISE, r.NAIVE_HOLD,
        r.NEGATIVE_SENTIMENT, r.ESCALATION_REQUEST,
    ),
    # v2: identity verification + investment advice + interruptions, churn insight,
    # and hold detection that honours "please hold" announcements (fewer false positives).
    "v2": (
        r.MISSING_GREETING, r.MISSING_CLOSING, r.MISSING_IDENTITY_CHECK, r.PROHIBITED_PROMISE,
        r.INVESTMENT_ADVICE, r.HOLD, r.INTERRUPTIONS, r.NEGATIVE_SENTIMENT, r.CHURN_RISK,
        r.ESCALATION_REQUEST,
    ),
}
# v3: the v2 rules with approximate phrase matching (~1 edit per 5 characters), to tolerate
# ASR misspellings. Whether that is worth the precision cost is decided by `make eval`.
RULESETS["v3"] = tuple(
    dataclasses.replace(rule, lexicon=rule.lexicon.fuzzy(0.2)) if hasattr(rule, "lexicon")
    else rule
    for rule in RULESETS["v2"]
)


@dataclass(frozen=True)
class QAReport:
    version: str
    score: float
    compliance: str  # "ok" | "violation"
    flags: list[str]
    findings: list[r.Finding]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "score": self.score,
            "compliance": self.compliance,
            "flags": self.flags,
            "findings": [f.to_dict() for f in self.findings],
            "metrics": self.metrics,
        }


def sentiment_score(call: CallView) -> float:
    """Crude lexicon polarity of the customer's speech in [-1, 1]. A trained classifier
    (e.g. ParsBERT fine-tuned on labelled calls) would replace this behind the same call."""
    texts = [u.text for u in call.by(CUSTOMER)]
    pos = sum(len(lx.POSITIVE.find(t)) for t in texts)
    neg = sum(len(lx.NEGATIVE.find(t)) for t in texts)
    if pos + neg == 0:
        return 0.0
    return round((pos - 2 * neg) / (pos + 2 * neg), 3)


def analyze(call: CallView, version: str) -> QAReport:
    try:
        ruleset = RULESETS[version]
    except KeyError:
        known = sorted(RULESETS)
        raise ValueError(f"unknown analyzer version {version!r}; known: {known}") from None

    findings = [rule.evaluate(call) for rule in ruleset]
    triggered = [f for f in findings if f.triggered]
    score = max(0.0, 100.0 - sum(f.weight for f in triggered))
    critical = any(f.severity == r.Severity.CRITICAL for f in triggered)
    features = call.features
    metrics = {
        "stereo": call.stereo,
        "utterances": len(call.utterances),
        "agent_talk_ratio": features.get("talk_ratio", {}).get(AGENT),
        "silence_ratio": features.get("silence_ratio"),
        "longest_silence_s": features.get("longest_silence_s"),
        "overlap_events": len(features.get("overlap_events", [])),
        "customer_sentiment": sentiment_score(call),
    }
    return QAReport(
        version=version,
        score=score,
        compliance="violation" if critical else "ok",
        flags=sorted(f.flag for f in triggered),
        findings=findings,
        metrics=metrics,
    )
