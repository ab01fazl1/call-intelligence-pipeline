"""Offline golden test: synthesize calls with known labels, run the real preprocessing and
analysis code on them (ASR replaced by the ground-truth transcript), compare flags.

This pins the contract between the data generator, the audio features and the rules
without any infrastructure.
"""

import random

import pytest

from callintel.analysis.model import CallView
from callintel.analysis.scoring import analyze
from callintel.audio.preprocess import preprocess_file, speakers_for
from callintel.synth.render import render_call, write_audio
from callintel.synth.scenarios import build
from callintel.synth.voices import AGENT_STYLES, CUSTOMER_STYLES, SyntheticVoices

CASES = {
    "clean": {"greeting": True, "verification": True, "prohibited_promise": False,
              "investment_advice": False, "negative": False, "hold": "none",
              "interruptions": False, "closing": True},
    "violations": {"greeting": False, "verification": False, "prohibited_promise": True,
                   "investment_advice": True, "negative": True, "churn": True,
                   "escalation": True, "hold": "unannounced", "interruptions": True,
                   "closing": False},
    "announced_hold": {"greeting": True, "verification": True, "prohibited_promise": False,
                       "investment_advice": False, "negative": False, "hold": "announced",
                       "interruptions": False, "closing": True},
}


@pytest.mark.parametrize("case", sorted(CASES))
async def test_flags_match_ground_truth(case, tmp_path):
    rng = random.Random(case)
    scenario = build(rng, case, output_format="wav_pcm16_8k_stereo", force=CASES[case])
    styles = {"agent": AGENT_STYLES[0], "customer": CUSTOMER_STYLES[AGENT_STYLES[0].voice][0]}
    call = await render_call(scenario, SyntheticVoices(), styles, rng)
    src = write_audio(tmp_path / case, call, scenario.output_format)

    out = tmp_path / "pp"
    out.mkdir()
    pp = preprocess_file(src, out, channel_map=["agent", "customer"], min_duration_s=1,
                         max_duration_s=3600)
    speakers = speakers_for(pp.channels, ["agent", "customer"])
    transcript = [{**s, "speaker": speakers[s["channel"]]} for s in call.segments]
    view = CallView.from_segments(pp.duration_s, transcript, pp.features)
    for version in ("v2", "v3"):
        assert set(analyze(view, version).flags) == scenario.expected_flags, version
