from collections.abc import Iterable
from dataclasses import dataclass

from callintel.text.normalize import normalize


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein distance (substitutions + deletions + insertions)."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def words(text: str) -> list[str]:
    return normalize(text).split()


@dataclass
class WER:
    errors: int = 0
    reference_words: int = 0

    def add(self, reference: str, hypothesis: str) -> float:
        ref, hyp = words(reference), words(hypothesis)
        errs = edit_distance(ref, hyp)
        self.errors += errs
        self.reference_words += len(ref)
        return errs / max(1, len(ref))

    @property
    def value(self) -> float:
        return self.errors / max(1, self.reference_words)


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, expected: bool, predicted: bool) -> None:
        if expected and predicted:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif expected:
            self.fn += 1
        else:
            self.tn += 1

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else None


def fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def markdown_table(header: Iterable[str], rows: Iterable[Iterable[object]]) -> str:
    header = list(header)
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)
