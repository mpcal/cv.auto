"""Provability, fidelity, position ordering, CEFR and degree comparison, hard facts
(P1 §4, §6.2, §6.5, §7, §8.2)."""

import re
from collections.abc import Iterable, Sequence
from typing import cast

from cvopt.domain.enums import (
    DEGREE_RANK,
    FIDELITY_RANK,
    MATCH_RANK,
    PROVABILITY_RANK,
    DegreeLevel,
    DocumentKind,
    EvidenceKind,
    Fidelity,
    MatchLevel,
    Provability,
)
from cvopt.domain.records import Claim, Position
from cvopt.domain.tables import load_table
from cvopt.domain.text import contains_term, normalize

# --- provability -----------------------------------------------------------

EVIDENCE_PROVABILITY: dict[EvidenceKind, Provability] = {
    EvidenceKind.REFERENCE_LETTER: Provability.HIGH,
    EvidenceKind.CERTIFICATE_OR_DIPLOMA: Provability.HIGH,
    EvidenceKind.REFERENCE_PERSON: Provability.HIGH,
    EvidenceKind.OWN_CV: Provability.MEDIUM,
    EvidenceKind.SELF_DECLARED: Provability.MEDIUM,
    EvidenceKind.LEARNABLE_DECLARED: Provability.LOW,
}

THIRD_PARTY_DOCUMENTS = {
    DocumentKind.REFERENCE_LETTER,
    DocumentKind.DIPLOMA,
    DocumentKind.CERTIFICATE,
}


def lowest(levels: Iterable[Provability]) -> Provability:
    return min(levels, key=PROVABILITY_RANK.__getitem__)


def highest(levels: Iterable[Provability]) -> Provability:
    return max(levels, key=PROVABILITY_RANK.__getitem__)


def claim_provability(kinds: Iterable[EvidenceKind]) -> Provability:
    """The highest level among the claim's evidence; `none` when it has none left."""
    return highest([Provability.NONE, *(EVIDENCE_PROVABILITY[k] for k in kinds)])


def fact_record_provability(document_kinds: Iterable[DocumentKind]) -> Provability:
    """`high` when a third-party source document backs the record, else `medium`."""
    if any(k in THIRD_PARTY_DOCUMENTS for k in document_kinds):
        return Provability.HIGH
    return Provability.MEDIUM


def statement_provability(
    fidelity: Fidelity, sources: Sequence[Provability], *, inferable: bool = False
) -> Provability:
    """P1 §7 matrix. Several sources take the lowest; `inferable` applies to invented only."""
    if fidelity is Fidelity.INVENTED:
        return Provability.LOW if inferable else Provability.NONE
    if not sources:
        raise ValueError(f"a {fidelity} statement needs at least one source claim")
    src = lowest(sources)
    if fidelity is Fidelity.EXAGGERATED:  # costs one level, never below low
        return Provability.MEDIUM if src is Provability.HIGH else Provability.LOW
    return src


def more_altered(a: Fidelity, b: Fidelity) -> Fidelity:
    """The final fidelity of a candidate: the more altered of intended and classified."""
    return max(a, b, key=FIDELITY_RANK.__getitem__)


# --- position ordering -----------------------------------------------------


def order_positions(positions: Iterable[Position]) -> list[Position]:
    """Current first, then by end month descending; ties by start month descending, then id."""
    return sorted(
        positions,
        key=lambda p: (p.end_month is None, p.end_month or "", p.start_month, p.id),
        reverse=True,
    )


# --- languages -------------------------------------------------------------

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2", "native"]
CEFR_RANK = {lvl: i for i, lvl in enumerate(CEFR_LEVELS)}


def cefr_rank(level: str) -> int:
    key = "native" if level.strip().casefold() == "native" else level.strip().upper()
    return CEFR_RANK[key]


def language_match(held: str, required: str) -> float:
    """At or above the required level = 1.0; one level below = 0.5; else 0."""
    diff = cefr_rank(held) - cefr_rank(required)
    return 1.0 if diff >= 0 else 0.5 if diff == -1 else 0.0


def language_code(name: str) -> str | None:
    """ISO 639-1 code for a language name or code ("Deutsch", "German", "de")."""
    key = normalize(name)
    names = cast(dict[str, str], load_table("languages")["names"])
    if key in names:
        return names[key]
    return key if key in names.values() else None


def cefr_level(text: str) -> str | None:
    """CEFR level for a level code or fluency text ("C1", "fließend", "native speaker")."""
    key = normalize(text)
    if key.upper() in CEFR_RANK:
        return key.upper()
    fluency = cast(dict[str, str], load_table("languages")["fluency"])
    return fluency.get(key)


# --- degrees ---------------------------------------------------------------


def degree_level(name: str) -> DegreeLevel:
    """Degree level for a degree name as written ("MSc", "Dr. rer. nat."); `other` if unknown."""
    names = cast(dict[str, str], load_table("degrees")["names"])
    key = normalize(name)
    if key in names:
        return DegreeLevel(names[key])
    first = key.split(" ")[0] if key else ""
    return DegreeLevel(names.get(first, DegreeLevel.OTHER))


def education_match(held: DegreeLevel, required: DegreeLevel | None, field: MatchLevel) -> float:
    """P1 §8.2. `field` is the field comparison (semantic or better = matching,
    partial = related); pass `exact` when the JD names no field. `other` never meets a level."""
    if required is None or required is DegreeLevel.OTHER:
        diff = 0
    elif held is DegreeLevel.OTHER:
        return 0.0
    else:
        diff = DEGREE_RANK[held] - DEGREE_RANK[required]
    rank = MATCH_RANK[field]
    if rank < MATCH_RANK[MatchLevel.PARTIAL]:
        return 0.0
    if diff >= 0:
        return 1.0 if rank >= MATCH_RANK[MatchLevel.SEMANTIC] else 0.6
    return 0.3 if diff == -1 else 0.0


# --- hard facts (P1 §6.5, P3 §10.4) ----------------------------------------------

NUMBER = re.compile(r"\d+(?:[.,'’]\d+)*")


def numbers(text: str) -> set[str]:
    return {m.replace("’", "'") for m in NUMBER.findall(text)}


class Vocabulary:
    """Named terms only: keywords of hard_skill and certification requirements (each
    with its synonyms as one group), keywords of skill claims, and employer names."""

    def __init__(self, groups: Iterable[Iterable[str]]) -> None:
        self._groups = [sorted({t for t in g if t.strip()}) for g in groups]
        self._groups = [g for g in self._groups if g]

    def terms_in(self, text: str) -> set[str]:
        return {t for g in self._groups for t in g if contains_term(t, text)}

    def term_or_equivalent_in(self, term: str, text: str) -> bool:
        groups = [g for g in self._groups if term in g] or [[term]]
        return any(contains_term(t, text) for g in groups for t in g)


def passes_hard_facts(
    text: str, fidelity: Fidelity, sources: Sequence[Claim], vocab: Vocabulary
) -> bool:
    """Sourced: every number and named term occurs in the sources. Invented: no digits."""
    if fidelity is Fidelity.INVENTED:
        return not re.search(r"\d", text)
    src_text = " ".join([s.text for s in sources] + [k for s in sources for k in s.keywords])
    if not numbers(text) <= numbers(src_text):
        return False
    return all(vocab.term_or_equivalent_in(t, src_text) for t in vocab.terms_in(text))


def language_display(code: str, level: str, cv_lang: str) -> tuple[str, str]:
    """(name, fluency) of a language record as written in a CV in `cv_lang`."""
    display = cast(dict[str, dict[str, str]], load_table("languages")["display"])
    names = display.get(cv_lang, display["en"])
    fluency = names["native"] if level == "native" else level
    return names.get(code, code), fluency
