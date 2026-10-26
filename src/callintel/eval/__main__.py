"""Score the running pipeline against synthetic ground truth.

    python -m callintel.eval --data data/synthetic --api http://localhost:8000 --out docs/eval.md

Uploaded calls are matched to ground truth by SHA-256. For each analyzer version present,
reports per-flag precision / recall and the transcript word error rate per ASR model.
"""

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from callintel.analysis.scoring import RULESETS
from callintel.eval.metrics import WER, Confusion, fmt, markdown_table


def load_truth(data: Path) -> dict[str, dict[str, Any]]:
    truth = {}
    for path in sorted((data / "ground_truth").glob("*.json")):
        item = json.loads(path.read_text())
        if item.get("expected_outcome", "completed") == "completed" and item["segments"]:
            truth[item["sha256"]] = item
    return truth


def fetch_calls(api: str, limit: int = 1000) -> list[dict[str, Any]]:
    with httpx.Client(base_url=api, timeout=30) as client:
        summaries = client.get("/v1/calls", params={"status": "completed", "limit": limit})
        summaries.raise_for_status()
        return [client.get(f"/v1/calls/{c['id']}").json() for c in summaries.json()]


def evaluate(truth: dict[str, dict[str, Any]], calls: list[dict[str, Any]]) -> str:
    matched = [(truth[c["sha256"]], c) for c in calls if c["sha256"] in truth]
    if not matched:
        return "No completed calls match the ground truth. Upload the dataset first (make demo)."

    wer_by_model: dict[str, WER] = defaultdict(WER)
    confusion: dict[str, dict[str, Confusion]] = defaultdict(lambda: defaultdict(Confusion))
    not_applicable: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    mono_errors: dict[str, int] = defaultdict(int)

    calls_by_model: dict[str, int] = defaultdict(int)
    for gt, call in matched:
        ref = " ".join(s["text"] for s in gt["segments"])
        for transcript in call.get("transcripts") or [call["transcript"]]:
            if transcript:
                hyp = " ".join(s["text"] for s in transcript["segments"])
                wer_by_model[transcript["model"]].add(ref, hyp)
                calls_by_model[transcript["model"]] += 1
        if not gt.get("flags_valid", True):
            continue
        expected = set(gt["expected_flags"])
        for qa in call["qa_results"]:
            version = f"{qa['analyzer_version']} on {qa['transcript_model']}"
            findings = {f["flag"]: f for f in qa["details"]["findings"]}
            for flag, finding in findings.items():
                if not finding["applicable"]:
                    not_applicable[version][flag] += 1
                    continue
                confusion[version][flag].add(flag in expected, finding["triggered"])
                if finding["low_confidence"] and (flag in expected) != finding["triggered"]:
                    mono_errors[version] += 1

    out = [
        "# Evaluation",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `python -m callintel.eval` "
        f"over {len(matched)} completed synthetic calls with ground truth.",
        "",
        "## Transcription",
        "",
        markdown_table(["ASR model", "calls", "reference words", "WER"], [
            (model, calls_by_model[model], w.reference_words, f"{w.value:.3f}")
            for model, w in sorted(wer_by_model.items())
        ]),
        "",
        "The mock ASR returns the ground truth, optionally with injected word errors "
        "(`MOCK_ASR_WER`), so its WER measures the injection, not a model. Real models are "
        "only comparable on real or TTS audio decoded by the model itself.",
    ]
    for version in sorted(confusion, key=lambda v: (list(RULESETS).index(v.split()[0])
                                                    if v.split()[0] in RULESETS else 99, v)):
        rows = []
        for flag, c in sorted(confusion[version].items()):
            rows.append((flag, c.tp, c.fp, c.fn, fmt(c.precision), fmt(c.recall), fmt(c.f1),
                         not_applicable[version].get(flag, 0)))
        out += ["", f"## QA rules: analyzer {version} transcripts", "",
                markdown_table(["flag", "TP", "FP", "FN", "precision", "recall", "F1", "n/a"],
                               rows)]
        if mono_errors[version]:
            out += ["", f"{mono_errors[version]} of the errors above are on mono recordings, "
                        "where the speaker of each utterance is unknown."]
    return "\n".join(out) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(prog="callintel.eval")
    p.add_argument("--data", default="data/synthetic")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--out", default=None, help="write the markdown report here too")
    args = p.parse_args()
    report = evaluate(load_truth(Path(args.data)), fetch_calls(args.api))
    print(report)
    if args.out:
        Path(args.out).write_text(report)


if __name__ == "__main__":
    main()
