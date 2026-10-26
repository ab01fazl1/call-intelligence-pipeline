"""Persian text normalization for matching and search.

Persian text from ASR, keyboards and databases mixes Arabic and Persian code points for the
same letters (ي/ی, ك/ک), writes the zero-width non-joiner (ZWNJ) inconsistently
("می‌کنم" / "میکنم" / "می کنم") and may carry diacritics, tatweel and two sets of
non-ASCII digits. Without normalization, keyword rules silently miss matches.
"""

import re
from dataclasses import dataclass, field

_CHAR_MAP = str.maketrans({
    "ي": "ی", "ى": "ی", "ئ": "ی",
    "ك": "ک",
    "ة": "ه", "ۀ": "ه",
    "أ": "ا", "إ": "ا", "ٱ": "ا",
    "ؤ": "و",
    "‌": " ",  # ZWNJ
    "‍": "",  # ZWJ
    "ـ": "",  # tatweel
    **{chr(0x06F0 + d): str(d) for d in range(10)},  # Persian digits
    **{chr(0x0660 + d): str(d) for d in range(10)},  # Arabic-Indic digits
})
_DIACRITICS = re.compile("[ً-ٰٟ]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = _DIACRITICS.sub("", text.translate(_CHAR_MAP)).lower()
    text = _NON_WORD.sub(" ", text).replace("_", " ")
    return _SPACES.sub(" ", text).strip()


def squash(text: str) -> str:
    """Normalized text with all spacing removed: "می کنم", "می‌کنم" and "میکنم" compare equal."""
    return normalize(text).replace(" ", "")


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def approx_contains(text: str, needle: str, max_errors: int) -> bool:
    """True if some substring of `text` is within `max_errors` edits of `needle`.

    Sellers' algorithm: edit-distance DP where a match may start anywhere in `text`
    (first row all zeros). O(len(needle) * len(text)).
    """
    if max_errors <= 0:
        return needle in text
    col = list(range(len(needle) + 1))
    for ch in text:
        prev_diag, col[0] = col[0], 0
        for i, nc in enumerate(needle, 1):
            prev_diag, col[i] = col[i], min(col[i] + 1, col[i - 1] + 1, prev_diag + (nc != ch))
        if col[-1] <= max_errors:
            return True
    return False


@dataclass(frozen=True)
class Lexicon:
    """A set of phrases matched against normalized text.

    Multi-word phrases match on the squashed form (robust to ZWNJ / spacing variants).
    Single words must match a whole token, so "راضی" (satisfied) does not fire inside
    "ناراضی" (dissatisfied). A trailing "*" turns a single word into a prefix match.

    `error_rate` > 0 tolerates about that many character edits per character of phrase
    (ASR misspellings such as «کود ملی» for «کد ملی»), at a cost in precision.
    """

    name: str
    phrases: tuple[str, ...]
    error_rate: float = 0.0
    _words: frozenset[str] = field(init=False, repr=False)
    _prefixes: tuple[str, ...] = field(init=False, repr=False)
    _squashed: tuple[tuple[str, str], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        words, prefixes, squashed = set(), [], []
        for phrase in self.phrases:
            norm = normalize(phrase.rstrip("*"))
            if " " in norm:
                squashed.append((phrase, norm.replace(" ", "")))
            elif phrase.endswith("*"):
                prefixes.append(norm)
            else:
                words.add(norm)
        object.__setattr__(self, "_words", frozenset(words))
        object.__setattr__(self, "_prefixes", tuple(prefixes))
        object.__setattr__(self, "_squashed", tuple(squashed))

    def fuzzy(self, error_rate: float) -> "Lexicon":
        return Lexicon(f"{self.name}~{error_rate:g}", self.phrases, error_rate)

    def _budget(self, target: str) -> int:
        return int(len(target) * self.error_rate)

    def find(self, text: str) -> list[str]:
        norm = normalize(text)
        tokens = norm.split()
        flat = norm.replace(" ", "")
        if self.error_rate <= 0:
            hits = [t for t in tokens if t in self._words]
            hits += [t for t in tokens for p in self._prefixes if t.startswith(p)]
            hits += [phrase for phrase, sq in self._squashed if sq in flat]
            return hits
        hits = [t for t in tokens for w in self._words
                if edit_distance(t, w) <= self._budget(w)]
        hits += [t for t in tokens for p in self._prefixes
                 if edit_distance(t[: len(p)], p) <= self._budget(p)]
        hits += [phrase for phrase, sq in self._squashed
                 if approx_contains(flat, sq, self._budget(sq))]
        return hits
