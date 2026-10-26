from callintel.analysis.model import CallView
from callintel.analysis.scoring import RULESETS, analyze


def view(segments, *, duration=120.0, features=None):
    return CallView.from_segments(duration, segments, features or {})


def seg(speaker, start, text, end=None):
    return {"speaker": speaker, "start": start, "end": end or start + 3, "text": text}


GOOD_CALL = [
    seg("agent", 0.5, "سلام، وقتتون بخیر. به مرکز تماس خوش آمدید."),
    seg("customer", 4, "سلام، سفارشم اجرا نشده."),
    seg("agent", 8, "لطفا کد ملی‌تون رو بفرمایید."),
    seg("customer", 12, "صفر صفر سه چهار."),
    seg("agent", 20, "سفارش شما در صف خرید است."),
    seg("agent", 100, "امر دیگه‌ای هست؟ روز خوبی داشته باشید."),
]


def flags(segments, version="v2", **kw):
    return analyze(view(segments, **kw), version).flags


def test_clean_call_scores_100():
    report = analyze(view(GOOD_CALL), "v2")
    assert report.flags == []
    assert report.score == 100
    assert report.compliance == "ok"


def test_prohibited_promise_is_critical():
    report = analyze(view([*GOOD_CALL, seg("agent", 50, "این سهم سود تضمینی داره")]), "v2")
    assert "prohibited_promise" in report.flags
    assert report.compliance == "violation"
    assert report.score == 60


def test_promise_said_by_customer_is_not_a_violation():
    assert "prohibited_promise" not in flags([*GOOD_CALL, seg("customer", 50, "سود تضمینی داره؟")])


def test_neutral_mention_of_dividend_is_not_a_promise():
    call = [*GOOD_CALL, seg("agent", 50, "سود نقدی مجمع واریز میشه")]
    assert "prohibited_promise" not in flags(call)


def test_investment_advice_only_in_v2():
    call = [*GOOD_CALL, seg("agent", 50, "به نظر من حتما بفروشید")]
    assert "investment_advice" in flags(call, "v2")
    assert "investment_advice" not in flags(call, "v1")


def test_greeting_must_be_early_and_by_agent():
    late = [seg("customer", 0.5, "سلام"), *GOOD_CALL[1:], seg("agent", 40, "سلام")]
    assert "missing_greeting" in flags(late)


def test_announced_hold_is_forgiven_in_v2_but_not_v1():
    call = [*GOOD_CALL, seg("agent", 30, "لطفا چند لحظه پشت خط باشید", end=33)]
    features = {"silences": [{"start_s": 33.2, "end_s": 70, "duration_s": 36.8}]}
    assert "unannounced_hold" not in flags(call, "v2", features=features)
    assert "unannounced_hold" in flags(call, "v1", features=features)


def test_unannounced_hold():
    features = {"silences": [{"start_s": 33.2, "end_s": 70, "duration_s": 36.8}]}
    assert "unannounced_hold" in flags(GOOD_CALL, "v2", features=features)


def test_customer_insights_do_not_lower_score():
    call = [*GOOD_CALL,
            seg("customer", 30, "واقعا افتضاحه، هیچ‌کس جواب نمیده"),
            seg("customer", 35, "حسابمو می‌بندم و میرم یه کارگزاری دیگه"),
            seg("customer", 40, "می‌خوام با سرپرستتون صحبت کنم")]
    report = analyze(view(call), "v2")
    assert {"negative_sentiment", "churn_risk", "escalation_request"} <= set(report.flags)
    assert report.score == 100
    assert report.metrics["customer_sentiment"] < 0


def test_interruptions_not_applicable_on_mono():
    mono = [{**s, "speaker": "unknown"} for s in GOOD_CALL]
    report = analyze(view(mono, features={"overlap_events": [{}] * 5}), "v2")
    finding = next(f for f in report.findings if f.flag == "excessive_interruptions")
    assert not finding.applicable and not finding.triggered
    assert all(f.low_confidence for f in report.findings)


def test_every_ruleset_flag_is_unique():
    for version, rules in RULESETS.items():
        names = [r.flag for r in rules]
        assert len(names) == len(set(names)), version
