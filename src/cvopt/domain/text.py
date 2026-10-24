"""Sentence splitting and text normalization."""

import re
import unicodedata
from typing import cast

from cvopt.domain.tables import load_table

_PROTECTED_DOT = "․"  # one dot leader: stands in for protected periods


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold()
    s = re.sub(r"[‐-―]", "-", s)  # unify hyphens and dashes
    return re.sub(r"\s+", " ", s).strip()


def contains_term(term: str, text: str) -> bool:
    """Whole-word, case-insensitive occurrence of `term` in `text`."""
    return re.search(rf"(?<!\w){re.escape(normalize(term))}(?!\w)", normalize(text)) is not None


def abbreviations(lang: str) -> list[str]:
    table = cast(dict[str, list[str]], load_table("abbreviations"))
    return table["common"] + table.get(lang, [])


def split_sentences(text: str, lang: str) -> list[str]:
    protected = text.strip()
    for abbr in sorted(abbreviations(lang), key=len, reverse=True):
        protected = re.sub(
            rf"(?<!\w){re.escape(abbr)}", abbr.replace(".", _PROTECTED_DOT), protected
        )
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"'(])", protected)
    return [p.replace(_PROTECTED_DOT, ".") for p in parts if p]
