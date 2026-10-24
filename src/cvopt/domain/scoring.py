"""The scoring engine (P1 §8.4–8.5): pure, given a match-value lookup."""

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date

from cvopt.domain.base import Model
from cvopt.domain.cv import CVElement, month_of, months_between
from cvopt.domain.enums import ElementKind as K
from cvopt.domain.enums import Importance, MatchLevel
from cvopt.domain.enums import RequirementCategory as C
from cvopt.domain.jd import Requirement
from cvopt.domain.text import contains_term

APPLIES: dict[C, set[K]] = {
    C.HARD_SKILL: {K.SKILL, K.HIGHLIGHT, K.HEADLINE, K.SUMMARY},
    C.SOFT_SKILL: {K.HIGHLIGHT, K.SUMMARY, K.SKILL},
    C.EXPERIENCE: {K.HIGHLIGHT, K.HEADLINE, K.SUMMARY},
    C.DOMAIN: {K.HIGHLIGHT, K.SUMMARY},
    C.EDUCATION: {K.EDUCATION},
    C.CERTIFICATION: {K.CERTIFICATE},
    C.LANGUAGE: {K.LANGUAGE},
    C.YEARS_EXPERIENCE: {K.HIGHLIGHT},  # through years_credit
}  # LOGISTICS is not scored

TEXT_CATEGORIES = {C.HARD_SKILL, C.SOFT_SKILL, C.EXPERIENCE, C.DOMAIN, C.YEARS_EXPERIENCE}

Value = Callable[[str, str], float]  # (requirement id, element id) -> match value, no placement


class ScoringParams(Model):
    weight_must: float = 3
    weight_nice: float = 1
    match_exact: float = 1.0
    match_synonym: float = 0.9
    match_semantic: float = 0.6
    match_partial: float = 0.3

    def weight(self, r: Requirement) -> float:
        return self.weight_must if r.importance is Importance.MUST else self.weight_nice

    def level_value(self, level: MatchLevel) -> float:
        return {
            MatchLevel.EXACT: self.match_exact,
            MatchLevel.SYNONYM: self.match_synonym,
            MatchLevel.SEMANTIC: self.match_semantic,
            MatchLevel.PARTIAL: self.match_partial,
            MatchLevel.NONE: 0.0,
        }[level]


class RequirementResult(Model):
    requirement_id: str
    weight: float
    coverage: float
    winners: dict[str, float]  # element id -> share of this requirement's points
    gap_points: float


class ScoreResult(Model):
    total: float
    requirements: list[RequirementResult]
    impacts: dict[str, float]  # element id -> pp


def text_level(req: Requirement, text: str, verdict: MatchLevel) -> MatchLevel:
    """verdict = judge_match's answer: semantic, partial or none (P2 §7.2)."""
    if verdict is not MatchLevel.NONE:
        if any(contains_term(k, text) for k in req.keywords):
            return MatchLevel.EXACT
        if any(contains_term(s, text) for s in req.synonyms):
            return MatchLevel.SYNONYM
    return verdict


def _month_range(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    out = []
    for _ in range(max(0, months_between(start, end) + 1)):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def years_credit(
    r: Requirement,
    elements: Sequence[CVElement],
    value: Value,
    params: ScoringParams,
    as_of: date,
) -> tuple[float, dict[str, float]]:
    """Coverage and per-highlight shares for a years-of-experience requirement (P3 §10.2)."""
    hits = [
        e
        for e in elements
        if e.kind is K.HIGHLIGHT
        and e.position_ref
        and e.start_month
        and value(r.id, e.id) >= params.match_semantic - 1e-9
    ]
    if not hits or not r.years:
        return 0.0, {}
    now = month_of(as_of)
    positions: dict[str, tuple[str, str]] = {}
    best: dict[str, CVElement] = {}
    for e in hits:
        assert e.position_ref and e.start_month
        positions[e.position_ref] = (e.start_month, min(e.end_month or now, now))
        cur = best.get(e.position_ref)
        if cur is None or (value(r.id, e.id), cur.id) > (value(r.id, cur.id), e.id):
            best[e.position_ref] = e
    assigned: dict[str, int] = defaultdict(int)
    union = sorted({m for s, end in positions.values() for m in _month_range(s, end)})
    for month in union:
        covering = [(s, ref) for ref, (s, end) in positions.items() if s <= month <= end]
        assigned[max(covering)[1]] += 1  # latest start wins; ties by ref
    coverage = min(1.0, len(union) / 12 / r.years)
    shares = {best[ref].id: n / len(union) for ref, n in sorted(assigned.items())}
    return coverage, shares


def score(
    elements: Sequence[CVElement],
    reqs: Sequence[Requirement],
    value: Value,
    params: ScoringParams,
    as_of: date,
) -> ScoreResult:
    scored = [r for r in reqs if r.category is not C.LOGISTICS]
    total_w = sum(params.weight(r) for r in scored)
    impacts: dict[str, float] = defaultdict(float)
    results = []
    for r in scored:
        w = params.weight(r)
        if r.category is C.YEARS_EXPERIENCE:
            coverage, shares = years_credit(r, elements, value, params, as_of)
        else:
            vals = {
                e.id: value(r.id, e.id) * e.placement
                for e in elements
                if e.kind in APPLIES[r.category]
            }
            coverage = max(vals.values(), default=0.0)
            best = [i for i, v in vals.items() if coverage > 0 and math.isclose(v, coverage)]
            shares = {i: 1 / len(best) for i in best}
        points = 100 * w * coverage / total_w if total_w else 0.0
        for i, share in shares.items():
            impacts[i] += points * share
        results.append(
            RequirementResult(
                requirement_id=r.id,
                weight=w,
                coverage=coverage,
                winners=shares,
                gap_points=100 * w * (1 - coverage) / total_w if total_w else 0.0,
            )
        )
    return ScoreResult(total=sum(impacts.values()), requirements=results, impacts=dict(impacts))
