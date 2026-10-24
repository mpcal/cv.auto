"""The Judge: match values for every requirement-element pair (P2 §7.2).

Text categories combine the keyword pass with judge_match; structured categories
use the deterministic rules. Verdicts are cached per pair (requirement text, element
text), so only unseen pairs are sent, in batches."""

from collections.abc import Sequence

from cvopt.domain.base import Model
from cvopt.domain.cv import CVElement
from cvopt.domain.enums import DegreeLevel, MatchLevel
from cvopt.domain.enums import RequirementCategory as C
from cvopt.domain.jd import Requirement
from cvopt.domain.rules import education_match, language_match
from cvopt.domain.scoring import APPLIES, TEXT_CATEGORIES, ScoringParams, text_level
from cvopt.domain.text import normalize
from cvopt.llm.runner import TaskRunner
from cvopt.llm.tasks.judge_match import (
    JUDGE_MATCH,
    ElementRef,
    JudgedRequirement,
    JudgeInput,
)

PAIR_TASK = "judge_match_pair"


class PairKey(Model):
    requirement: str
    element: str


class PairVerdict(Model):
    level: MatchLevel


class Judge:
    def __init__(self, runner: TaskRunner, params: ScoringParams, batch_size: int) -> None:
        self._runner = runner
        self._params = params
        self._batch = batch_size

    async def verdicts(self, r: Requirement, texts: Sequence[str]) -> dict[str, MatchLevel]:
        """judge_match's level per distinct text, from the pair cache or the model."""
        out: dict[str, MatchLevel] = {}
        missing: list[str] = []
        for t in sorted(set(texts)):
            hit = await self._runner.cached(
                PAIR_TASK,
                JUDGE_MATCH.version,
                JUDGE_MATCH.tier,
                PairKey(requirement=r.text, element=t),
                PairVerdict,
            )
            if hit is None:
                missing.append(t)
            else:
                out[t] = hit.level
        req = JudgedRequirement(
            text=r.text, category=r.category, keywords=r.keywords, synonyms=r.synonyms
        )
        for start in range(0, len(missing), self._batch):
            chunk = missing[start : start + self._batch]
            refs = {f"E{i}": t for i, t in enumerate(chunk, start=1)}
            res = await self._runner.run(
                JUDGE_MATCH,
                JudgeInput(
                    requirement=req, elements=[ElementRef(ref=k, text=v) for k, v in refs.items()]
                ),
            )
            for v in res.verdicts:
                level = MatchLevel(v.level)
                out[refs[v.ref]] = level
                await self._runner.store(
                    PAIR_TASK,
                    JUDGE_MATCH.version,
                    JUDGE_MATCH.tier,
                    PairKey(requirement=r.text, element=refs[v.ref]),
                    PairVerdict(level=level),
                )
        return out

    async def values(
        self, reqs: Sequence[Requirement], elements: Sequence[CVElement]
    ) -> dict[tuple[str, str], float]:
        """Match value (without placement) of every applicable pair; absent pairs are 0."""
        p = self._params
        values: dict[tuple[str, str], float] = {}
        for r in reqs:
            if r.category is C.LOGISTICS:
                continue
            els = [e for e in elements if e.kind in APPLIES[r.category]]
            if not els:
                continue
            if r.category in TEXT_CATEGORIES:
                verdicts = await self.verdicts(r, [e.text for e in els])
                for e in els:
                    values[r.id, e.id] = p.level_value(text_level(r, e.text, verdicts[e.text]))
            elif r.category is C.LANGUAGE:
                for e in els:
                    held, code = e.meta.get("level", ""), e.meta.get("language_code", "")
                    if code and held and code == r.language_code:
                        values[r.id, e.id] = (
                            language_match(held, r.language_level) if r.language_level else 1.0
                        )
            elif r.category is C.EDUCATION:
                await self._education(r, els, values)
            elif r.category is C.CERTIFICATION:
                await self._certification(r, els, values)
        return values

    async def _education(
        self, r: Requirement, els: Sequence[CVElement], values: dict[tuple[str, str], float]
    ) -> None:
        if r.degree_fields:
            # Only the field is judged; the degree level is compared by the rules.
            fields_req = r.model_copy(
                update={
                    "text": f"Degree field: {' or '.join(r.degree_fields)}",
                    "keywords": r.degree_fields,
                    "synonyms": [],
                }
            )
            verdicts = await self.verdicts(fields_req, [e.meta.get("area") or e.text for e in els])
        for e in els:
            held = DegreeLevel(e.meta.get("degree_level", DegreeLevel.OTHER))
            area = e.meta.get("area") or e.text
            field = (
                text_level(fields_req, area, verdicts[area])
                if r.degree_fields
                else MatchLevel.EXACT
            )
            values[r.id, e.id] = education_match(held, r.degree_level, field)

    async def _certification(
        self, r: Requirement, els: Sequence[CVElement], values: dict[tuple[str, str], float]
    ) -> None:
        names = {normalize(n) for n in [*r.certificate_names, *r.keywords]}
        unmatched = [e for e in els if normalize(e.text) not in names]
        verdicts = await self.verdicts(r, [e.text for e in unmatched]) if unmatched else {}
        for e in els:
            same = normalize(e.text) in names or verdicts.get(e.text) is MatchLevel.SEMANTIC
            values[r.id, e.id] = 1.0 if same else 0.0
