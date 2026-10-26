"""Evaluate preprocessing + rules on a generated dataset without the stack.

Runs the real preprocessing on each file, then feeds every analyzer version the
ground-truth transcript, optionally degraded with injected word errors. This isolates rule
and audio-feature quality from ASR quality, and shows how fast each rule decays as ASR
gets worse.

    uv run python scripts/offline_eval.py data/synthetic                 # perfect transcripts
    uv run python scripts/offline_eval.py data/synthetic --wer 0 0.1 0.2 0.3 --out docs/rule-robustness.md
"""

import argparse
import json
import random
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from callintel.analysis.model import CallView
from callintel.analysis.scoring import RULESETS, analyze
from callintel.asr.mock import corrupt
from callintel.audio.preprocess import preprocess_file, speakers_for
from callintel.eval.metrics import WER, Confusion, fmt, markdown_table


def load(dataset: Path) -> list[tuple[dict, float, dict, list[str]]]:
    calls = []
    for gt_path in sorted((dataset / "ground_truth").glob("*.json")):
        gt = json.loads(gt_path.read_text())
        if gt.get("expected_outcome") != "completed" or not gt.get("flags_valid", True):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            pp = preprocess_file(dataset / gt["file"], Path(tmp), channel_map=["agent", "customer"],
                                 min_duration_s=1, max_duration_s=4 * 3600)
        calls.append((gt, pp.duration_s, pp.features, speakers_for(pp.channels, ["agent", "customer"])))
    return calls


def evaluate(calls, wer: float, verbose: bool):
    confusion = {v: defaultdict(Confusion) for v in RULESETS}
    measured = WER()
    for gt, duration, features, speakers in calls:
        rng = random.Random(f"{gt['sha256']}:{wer}")
        segments = []
        for s in gt["segments"]:
            text = corrupt(s["text"], wer, rng)
            measured.add(s["text"], text)
            speaker = speakers[s["channel"]] if len(speakers) == 2 else "unknown"
            segments.append({**s, "text": text, "speaker": speaker})
        view = CallView.from_segments(duration, segments, features)
        for version in RULESETS:
            report = analyze(view, version)
            for f in report.findings:
                if f.applicable:
                    confusion[version][f.flag].add(f.flag in gt["expected_flags"], f.triggered)
            wrong = set(report.flags) ^ set(gt["expected_flags"])
            if verbose and wrong and version == max(RULESETS):
                print(f"  wer={wer} {gt['name']} ({gt['format']}): mismatched {sorted(wrong)}")
    return confusion, measured.value


def main(args: argparse.Namespace) -> None:
    calls = load(Path(args.dataset))
    sweep = {wer: evaluate(calls, wer, args.verbose) for wer in args.wer}
    latest = max(RULESETS)
    out = [f"# Rule robustness\n\nGenerated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by "
           f"`scripts/offline_eval.py` over {len(calls)} synthetic calls. Audio features come "
           "from the real preprocessing; transcripts are ground truth with injected word errors "
           "(deletion / substitution / insertion), so this measures the rules, not an ASR model.\n"]
    for version in RULESETS:
        flags = sorted({f for conf, _ in sweep.values() for f in conf[version]})
        first = next(iter(sweep.values()))[0][version]
        header = ["flag", "positives"] + [f"recall @ WER {m:.2f}" for _, m in sweep.values()]
        rows = [[flag, first[flag].tp + first[flag].fn]
                + [fmt(conf[version][flag].recall) for conf, _ in sweep.values()]
                for flag in flags]
        out += [f"## Analyzer {version}: recall\n", markdown_table(header, rows), ""]
        if version == latest:
            header = ["flag"] + [f"precision @ WER {m:.2f}" for _, m in sweep.values()]
            rows = [[flag] + [fmt(conf[version][flag].precision) for conf, _ in sweep.values()]
                    for flag in flags]
            out += [f"## Analyzer {version}: precision\n", markdown_table(header, rows), ""]
    out += ["Recall at WER 0 is an upper bound by construction: the generator and the rules share "
            "the phrase inventory. The slope as WER grows is the informative part: phrase rules "
            "fail when any word of the phrase is misrecognized, which is the case for adding a "
            "model-based detector next to them (ADR 0014).", ""]
    report = "\n".join(out)
    print(report)
    if args.out:
        Path(args.out).write_text(report)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("dataset", nargs="?", default="data/synthetic")
    p.add_argument("--wer", type=float, nargs="+", default=[0.0])
    p.add_argument("--out", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    main(p.parse_args())
