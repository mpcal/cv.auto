"""Greedy selection of candidate statements within a line budget (P2 §6.2).

A candidate's gain is the score of the selection with it minus the score without it,
both computed by the same engine that scores finished CVs."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from cvopt.domain.candidates import Candidate
from cvopt.domain.cv import CVElement
from cvopt.domain.enums import FIDELITY_RANK, PROVABILITY_RANK, Fidelity, Slot
from cvopt.domain.jd import Requirement
from cvopt.domain.scoring import ScoringParams, Value, score
from cvopt.domain.text import normalize


@dataclass(frozen=True)
class SelectionParams:
    budget_lines: float
    exaggeration_min_gain_pp: float = 1.0
    tie_window_pp: float = 0.1


@dataclass
class Selection:
    chosen: list[Candidate] = field(default_factory=list)
    gains: dict[str, float] = field(default_factory=dict)  # gain when picked
    # exaggerated picks: the best gain of a non-exaggerated candidate with the same sources
    alternative_gain: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)  # why a candidate was not chosen
    lines: float = 0.0


def tie_key(c: Candidate) -> tuple[int, int, int, str]:
    """Less altered, then better provable, then shorter, then id."""
    return (FIDELITY_RANK[c.fidelity], -PROVABILITY_RANK[c.provability], len(c.text), c.id)


def _skill_key(c: Candidate) -> str:
    return normalize(c.text)


class Optimizer:
    def __init__(
        self,
        reqs: Sequence[Requirement],
        fixed: Sequence[CVElement],
        element_of: Callable[[Candidate], CVElement],
        value: Value,
        scoring: ScoringParams,
        as_of: date,
    ) -> None:
        self._reqs = reqs
        self._fixed = list(fixed)
        self._element_of = element_of
        self._value = value
        self._scoring = scoring
        self._as_of = as_of

    def total(self, chosen: Sequence[Candidate]) -> float:
        els = self._fixed + [self._element_of(c) for c in chosen]
        return score(els, self._reqs, self._value, self._scoring, self._as_of).total

    def _eligible(self, c: Candidate, sel: Selection, budget: float) -> str | None:
        """None if eligible, else the reason it is not."""
        if sel.lines + c.lines > budget + 1e-9:
            return "budget"
        if c.slot is Slot.HIGHLIGHT:
            used = {s for x in sel.chosen if x.slot is Slot.HIGHLIGHT for s in x.source_claim_ids}
            if used & set(c.source_claim_ids):
                return "superseded"
        if c.slot is Slot.SKILL and any(
            x.slot is Slot.SKILL and _skill_key(x) == _skill_key(c) for x in sel.chosen
        ):
            return "superseded"
        if c.assertion_key and any(x.assertion_key == c.assertion_key for x in sel.chosen):
            return "superseded"
        return None

    def select(
        self, cands: Sequence[Candidate], p: SelectionParams, start: Selection | None = None
    ) -> Selection:
        sel = start or Selection()
        by_id = {c.id: c for c in cands}
        while True:
            base = self.total(sel.chosen)
            gains: dict[str, float] = {}
            for c in cands:
                if c in sel.chosen:
                    continue
                reason = self._eligible(c, sel, p.budget_lines)
                if reason:
                    sel.reasons[c.id] = reason
                    continue
                gains[c.id] = self.total([*sel.chosen, c]) - base
            alternatives = self._drop_weak_exaggerations(gains, by_id, p, sel)
            if not gains or max(gains.values()) <= 1e-9:
                for cid, g in gains.items():
                    sel.reasons.setdefault(cid, "no gain" if g <= 1e-9 else "not selected")
                return sel
            ratio = {cid: g / max(by_id[cid].lines, 1e-6) for cid, g in gains.items()}
            top = max(ratio.values())
            tied = [by_id[cid] for cid, r in ratio.items() if r >= top - p.tie_window_pp]
            pick = min(tied, key=tie_key)
            sel.chosen.append(pick)
            sel.gains[pick.id] = gains[pick.id]
            sel.lines += pick.lines
            sel.reasons.pop(pick.id, None)
            if pick.fidelity is Fidelity.EXAGGERATED:
                sel.alternative_gain[pick.id] = alternatives.get(pick.id, 0.0)

    def _drop_weak_exaggerations(
        self,
        gains: dict[str, float],
        by_id: dict[str, Candidate],
        p: SelectionParams,
        sel: Selection,
    ) -> dict[str, float]:
        """Removes exaggerated candidates that do not beat the best non-exaggerated
        candidate with the same source claims by the minimum gain; returns that best
        alternative gain for the ones kept."""
        best_plain: dict[frozenset[str], float] = {}
        for cid, g in gains.items():
            c = by_id[cid]
            if c.fidelity in (Fidelity.UNMODIFIED, Fidelity.REPHRASED):
                key = frozenset(c.source_claim_ids)
                best_plain[key] = max(best_plain.get(key, 0.0), g)
        alternatives: dict[str, float] = {}
        for cid in list(gains):
            c = by_id[cid]
            if c.fidelity is not Fidelity.EXAGGERATED:
                continue
            alt = best_plain.get(frozenset(c.source_claim_ids), 0.0)
            if gains[cid] < alt + p.exaggeration_min_gain_pp:
                del gains[cid]
                sel.reasons[cid] = "exaggeration gains too little"
            else:
                alternatives[cid] = alt
        return alternatives

    def fill(
        self,
        sel: Selection,
        fillers: dict[str, list[Candidate]],
        position_order: Sequence[str],
        budget: float,
        relevance: Callable[[Candidate], float],
    ) -> Selection:
        """Positions without highlights get their most relevant sourced, unmodified or
        rephrased candidate if it fits, most recent position first (P1 §6.2)."""
        for pid in position_order:
            if any(c.slot is Slot.HIGHLIGHT and c.position_id == pid for c in sel.chosen):
                continue
            options = [
                c
                for c in fillers.get(pid, [])
                if c.fidelity in (Fidelity.UNMODIFIED, Fidelity.REPHRASED)
                and self._eligible(c, sel, budget) is None
            ]
            if options:
                pick = min(options, key=lambda c: (-relevance(c), *tie_key(c)))
                sel.chosen.append(pick)
                sel.gains[pick.id] = self.total(sel.chosen) - self.total(sel.chosen[:-1])
                sel.lines += pick.lines
                sel.reasons.pop(pick.id, None)
        return sel
