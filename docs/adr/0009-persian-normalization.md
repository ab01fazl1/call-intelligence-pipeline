# 0009: In-house Persian text normalization for matching and search

Status: accepted · 2026-10-26

## Context
The same Persian word appears in several byte forms depending on keyboard, OS, ASR model and
database:

| issue | example |
|---|---|
| Arabic vs. Persian code points | ي / ی, ك / ک, ة / ه |
| zero-width non-joiner (ZWNJ) used, omitted, or replaced by a space | می‌کنم / میکنم / می کنم |
| diacritics and tatweel | سـلامٌ |
| three digit systems | ۱۲۳ / ١٢٣ / 123 |

A keyword rule that looks for «تضمین می‌کنم» ("I guarantee") silently misses «تضمین میکنم».
For a compliance detector, a silent miss is the worst failure mode.

## Decision
- `text/normalize.py` maps these to one canonical form. Both lexicon phrases and transcripts
  go through it.
- Matching (`Lexicon`):
  - multi-word phrases compare on a **squashed** form with all spacing and ZWNJ removed, so
    every spacing variant matches;
  - single words must match a **whole token** («راضی» "satisfied" must not fire inside
    «ناراضی» "dissatisfied"), with an explicit `*` suffix for prefix matches (Persian suffixes:
    افتضاح → افتضاحه).
- Search uses the same normalization on both the stored `search_text` and the query.

## Consequences
+ About 60 lines, zero dependencies, fully unit-tested against the cases above.
- Not a full NLP toolkit: no lemmatization or part-of-speech tagging. If the rules move to
  morphology-aware matching, `hazm` or `parsivar` would be the step up.

## Alternatives considered
- **hazm**: the standard Persian NLP library. Its normalizer is good, but the package pulls a
  large dependency tree for what is a character-mapping problem here.
