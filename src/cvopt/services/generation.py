"""GenerationService (F4, with F6): the generation pipeline of P2 §6.

Load → translate → judge relevance → generate candidates → verify → judge candidates →
select → headline and summary → assemble and score → validate and render (loop) → write.
"""

import asyncio
import json
import logging
import math
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.candidates import Annotation, Candidate
from cvopt.domain.cv import CVElement, JSONResume, Placement, highlight_placement, parse_elements
from cvopt.domain.enums import (
    ClaimStatus,
    ClaimType,
    ElementKind,
    Fidelity,
    MatchLevel,
    Provability,
    RecordType,
    Slot,
)
from cvopt.domain.enums import RequirementCategory as C
from cvopt.domain.jd import JobDescription, Requirement
from cvopt.domain.optimizer import Optimizer, Selection, SelectionParams, tie_key
from cvopt.domain.records import Certificate, Claim, Education, Language, Position, Profile
from cvopt.domain.report import DroppedClaim, Report, StatementInfo, build_report, report_csv
from cvopt.domain.rules import (
    Vocabulary,
    fact_record_provability,
    language_display,
    more_altered,
    order_positions,
    passes_hard_facts,
    statement_provability,
)
from cvopt.domain.run import RunInfo
from cvopt.domain.scoring import ScoreResult, ScoringParams, score
from cvopt.domain.text import contains_term, normalize
from cvopt.domain.validator import PlacedStatement, validate_statements
from cvopt.errors import InputError, ValidationFailed
from cvopt.llm.judge import Judge
from cvopt.llm.runner import TaskRunner, TaskRunnerFactory
from cvopt.llm.task import Task
from cvopt.llm.tasks.classify_fidelity import CLASSIFY_FIDELITY, FidelityInput, FidelityItem
from cvopt.llm.tasks.generate_inventions import (
    GENERATE_INVENTIONS,
    InventionsInput,
    PositionText,
)
from cvopt.llm.tasks.generate_variants import (
    GENERATE_VARIANTS,
    ClaimText,
    ReqRef,
    VariantsInput,
)
from cvopt.llm.tasks.group_skills import GROUP_SKILLS, GroupInput
from cvopt.llm.tasks.translate import TRANSLATE, TextRef, TranslateInput
from cvopt.llm.tasks.write_summary import WRITE_SUMMARY, SummaryInput
from cvopt.ports import FileStore, RepositoryFactory
from cvopt.services.render import RenderService, build_render_model
from cvopt.services.scoring import (
    ScoringService,
    load_requirements,
    placement,
    read_json,
    scoring_params,
)

log = logging.getLogger(__name__)

INVENTABLE = {C.HARD_SKILL, C.SOFT_SKILL, C.EXPERIENCE, C.DOMAIN}
TASKS: list[Task[Any, Any]] = [
    TRANSLATE,
    GENERATE_VARIANTS,
    GENERATE_INVENTIONS,
    CLASSIFY_FIDELITY,
    WRITE_SUMMARY,
    GROUP_SKILLS,
]
SLOT_KIND = {
    Slot.HEADLINE: ElementKind.HEADLINE,
    Slot.SUMMARY: ElementKind.SUMMARY,
    Slot.HIGHLIGHT: ElementKind.HIGHLIGHT,
    Slot.SKILL: ElementKind.SKILL,
}
CLASSIFY_BATCH = 10
TRANSLATE_BATCH = 20


def dump(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


@dataclass
class Facts:
    """The user's confirmed data, as generation sees it."""

    profile: Profile
    positions: list[Position]  # ordered as in P1 §6.2
    education: list[Education]
    languages: list[Language]
    certificates: list[Certificate]
    claims: dict[str, Claim]  # confirmed claims, text in the JD's language
    original_text: dict[str, str]  # claim id -> stored text
    fact_provability: dict[tuple[RecordType, str], Provability]


@dataclass
class Pool:
    """Candidate statements and everything known about them."""

    cands: dict[str, Candidate] = field(default_factory=dict)
    intended: dict[str, Fidelity] = field(default_factory=dict)
    classified: dict[str, str] = field(default_factory=dict)  # reason from classify_fidelity
    discarded: dict[str, str] = field(default_factory=dict)  # id -> why
    inferable: dict[str, bool] = field(default_factory=dict)
    filler: set[str] = field(default_factory=set)  # only usable as filler
    counter: int = 0

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter:03d}"

    def add(self, c: Candidate, intended: Fidelity, *, inferable: bool = False) -> Candidate:
        self.cands[c.id] = c
        self.intended[c.id] = intended
        self.inferable[c.id] = inferable
        return c

    def live(self, *slots: Slot) -> list[Candidate]:
        return [
            c
            for c in self.cands.values()
            if c.id not in self.discarded
            and c.id not in self.filler
            and (not slots or c.slot in slots)
        ]


@dataclass(frozen=True)
class GenerationResult:
    run_dir: Path
    score: float
    files: list[Path]


class GenerationService:
    def __init__(
        self,
        repos: RepositoryFactory,
        runners: TaskRunnerFactory,
        files: FileStore,
        scoring: ScoringService,
        render: RenderService,
        settings: Settings,
    ) -> None:
        self._repos = repos
        self._runners = runners
        self._files = files
        self._scoring = scoring
        self._render = render
        self._s = settings

    # --- entry point -----------------------------------------------------------------

    async def generate(self, user: UserContext, run_ref: str) -> GenerationResult:
        run_dir = self._files.run_dir(user, run_ref)
        info = RunInfo.model_validate(read_json(run_dir / "run.json", "run.json"))
        jd = load_requirements(run_dir / "requirements.json")
        pipeline = _Pipeline(self, user, jd, info.as_of)
        await pipeline.run()
        written = pipeline.write(run_dir, info)
        return GenerationResult(run_dir, pipeline.final_score.total, written)


class _Pipeline:
    def __init__(
        self, svc: GenerationService, user: UserContext, jd: JobDescription, as_of: date
    ) -> None:
        self.svc, self.user, self.jd, self.as_of = svc, user, jd, as_of
        self.s = svc._s
        self.lang = jd.language
        self.runner: TaskRunner = svc._runners.for_user(user)
        self.params: ScoringParams = scoring_params(self.s)
        self.placement: Placement = placement(self.s)
        self.judge = Judge(self.runner, self.params, self.s.matching.judge_batch_size)
        self.pool = Pool()
        self.values: dict[tuple[str, str], float] = {}
        self.relevance: dict[str, dict[str, float]] = {}  # claim id -> req id -> value
        self.blocked: set[str] = set()  # candidates dropped by the validate/render loop
        self.fallback: set[str] = set()  # unmodified summary sentences of last resort

    # --- step 1: load ------------------------------------------------------------------

    async def load(self) -> Facts:
        repo = self.svc._repos.for_user(self.user)
        claims = await repo.list_claims(status=ClaimStatus.CONFIRMED)
        docs = {d.id: d.kind for d in await repo.list_documents()}
        fact_prov: dict[tuple[RecordType, str], Provability] = {}
        education = await repo.list_education()
        languages = await repo.list_languages()
        certificates = await repo.list_certificates()
        for rtype, records in (
            (RecordType.EDUCATION, education),
            (RecordType.LANGUAGE, languages),
            (RecordType.CERTIFICATE, certificates),
        ):
            for r in records:
                kinds = [
                    docs[d] for d in await repo.fact_source_documents(rtype, r.id) if d in docs
                ]
                fact_prov[rtype, r.id] = fact_record_provability(kinds)
        return Facts(
            profile=await repo.get_profile(),
            positions=order_positions(await repo.list_positions()),
            education=education,
            languages=languages,
            certificates=certificates,
            claims={c.id: c for c in claims},
            original_text={c.id: c.text for c in claims},
            fact_provability=fact_prov,
        )

    # --- step 2: translate -------------------------------------------------------------

    async def translate(self, facts: Facts) -> None:
        foreign = sorted(
            (c for c in facts.claims.values() if c.text_lang != self.lang), key=lambda c: c.id
        )
        for start in range(0, len(foreign), TRANSLATE_BATCH):
            chunk = foreign[start : start + TRANSLATE_BATCH]
            refs = {f"T{i}": c for i, c in enumerate(chunk, start=1)}
            out = await self.runner.run(
                TRANSLATE,
                TranslateInput(
                    texts=[TextRef(ref=r, text=c.text) for r, c in refs.items()],
                    target_language=self.lang,
                ),
            )
            for t in out.translations:
                c = refs[t.ref]
                facts.claims[c.id] = c.model_copy(update={"text": t.text, "text_lang": self.lang})

    # --- elements --------------------------------------------------------------------

    def position(self, facts: Facts, pid: str | None) -> Position | None:
        return next((p for p in facts.positions if p.id == pid), None)

    def element(self, facts: Facts, c: Candidate) -> CVElement:
        kind = SLOT_KIND[c.slot]
        pos = self.position(facts, c.position_id) if c.slot is Slot.HIGHLIGHT else None
        if kind is ElementKind.HIGHLIGHT:
            assert pos is not None
            plc = highlight_placement(pos.end_month, self.as_of, self.placement)
        elif kind is ElementKind.SKILL:
            plc = self.placement.skills
        else:
            plc = self.placement.headline_summary
        return CVElement(
            id=f"cand:{c.id}",
            kind=kind,
            text=c.text,
            position_ref=pos.id if pos else None,
            start_month=pos.start_month if pos else None,
            end_month=pos.end_month if pos else None,
            placement=plc,
        )

    def lines(self, text: str, slot: Slot) -> float:
        cpl = self.s.layout.chars_per_line
        if slot is Slot.SKILL:
            return (len(text) + 2) / cpl
        if slot is Slot.SUMMARY:
            return (len(text) + 1) / cpl
        return float(math.ceil(len(text) / cpl))

    # --- step 3: relevance ---------------------------------------------------------------

    async def judge_relevance(self, facts: Facts) -> None:
        els = []
        for c in facts.claims.values():
            pos = self.position(facts, c.position_id)
            kind = ElementKind.SKILL if c.type is ClaimType.SKILL else ElementKind.HIGHLIGHT
            els.append(
                CVElement(
                    id=f"claim:{c.id}",
                    kind=kind,
                    text=c.text,
                    placement=1.0,
                    position_ref=pos.id if pos else None,
                    start_month=pos.start_month if pos else None,
                    end_month=pos.end_month if pos else None,
                )
            )
            if kind is ElementKind.SKILL:  # skills also count as highlights for experience
                els.append(
                    els[-1].model_copy(
                        update={"id": f"claimh:{c.id}", "kind": ElementKind.HIGHLIGHT}
                    )
                )
        values = await self.judge.values(self.jd.requirements, els)
        for (rid, eid), v in values.items():
            cid = eid.split(":", 1)[1]
            cur = self.relevance.setdefault(cid, {})
            cur[rid] = max(cur.get(rid, 0.0), v)

    def relevant(self, cid: str) -> bool:
        return (
            max(self.relevance.get(cid, {}).values(), default=0.0)
            >= self.params.match_partial - 1e-9
        )

    def requirements_of(self, cids: Sequence[str]) -> list[Requirement]:
        rids = {
            r
            for cid in cids
            for r, v in self.relevance.get(cid, {}).items()
            if v >= self.params.match_partial - 1e-9
        }
        return [r for r in self.jd.requirements if r.id in rids]

    # --- step 4: candidates --------------------------------------------------------------

    def candidate(
        self,
        facts: Facts,
        slot: Slot,
        text: str,
        sources: list[str],
        intended: Fidelity,
        position_id: str | None,
        *,
        inferred_from: Sequence[str] = (),
        assertion_key: str | None = None,
        inferable: bool = False,
        filler: bool = False,
    ) -> Candidate:
        prefix = {Slot.HIGHLIGHT: "h", Slot.SKILL: "k", Slot.HEADLINE: "t", Slot.SUMMARY: "s"}
        c = Candidate(
            id=self.pool.new_id(prefix[slot]),
            slot=slot,
            position_id=position_id,
            text=text.strip(),
            source_claim_ids=sources,
            fidelity=intended,
            provability=Provability.NONE,
            inferred_from=list(inferred_from),
            assertion_key=assertion_key,
            lines=self.lines(text.strip(), slot),
        )
        self.pool.add(c, intended, inferable=inferable)
        if filler:
            self.pool.filler.add(c.id)
        return c

    def jd_spelling(self, text: str) -> str:
        """A skill keyword spelled as in the JD where one of its terms matches."""
        for r in self.jd.requirements:
            if r.category not in (C.HARD_SKILL, C.SOFT_SKILL):
                continue
            for term in [*r.keywords, *r.synonyms]:
                if normalize(term) == normalize(text) or contains_term(term, text):
                    exact = [k for k in r.keywords if normalize(k) == normalize(term)]
                    return exact[0] if exact else (r.keywords[0] if len(text) < 40 else text)
        return text

    async def generate_candidates(self, facts: Facts) -> None:
        seen_skills: set[str] = set()
        by_position: dict[str, list[Claim]] = {}
        for c in sorted(facts.claims.values(), key=lambda c: c.id):
            if c.type is ClaimType.SKILL:
                if not self.relevant(c.id):
                    continue
                text = self.jd_spelling(c.text)
                if normalize(text) in seen_skills:
                    continue
                seen_skills.add(normalize(text))
                fid = Fidelity.UNMODIFIED if text == c.text else Fidelity.REPHRASED
                self.candidate(facts, Slot.SKILL, text, [c.id], fid, None)
            elif c.position_id:
                relevant = self.relevant(c.id)
                self.candidate(
                    facts,
                    Slot.HIGHLIGHT,
                    c.text,
                    [c.id],
                    Fidelity.UNMODIFIED,
                    c.position_id,
                    filler=not relevant,
                )
                if relevant:
                    by_position.setdefault(c.position_id, []).append(c)
        jobs = []
        for pid, claims in by_position.items():
            pos = self.position(facts, pid)
            assert pos is not None
            for start in range(0, len(claims), 3):
                jobs.append((pid, pos, claims[start : start + 3]))
        results = await asyncio.gather(*(self._variants(p, cl) for _, p, cl in jobs))
        for (pid, _, claims), out in zip(jobs, results, strict=True):
            refs = {f"C{i}": c.id for i, c in enumerate(claims, start=1)}
            for v in out.variants:
                self.candidate(
                    facts,
                    Slot.HIGHLIGHT,
                    v.text,
                    sorted(refs[r] for r in v.source_refs),
                    Fidelity(v.fidelity),
                    pid,
                )

    async def _variants(self, pos: Position, claims: list[Claim]) -> Any:
        return await self.runner.run(
            GENERATE_VARIANTS,
            VariantsInput(
                position=f"{pos.title} at {pos.employer}",
                claims=[ClaimText(ref=f"C{i}", text=c.text) for i, c in enumerate(claims, start=1)],
                requirements=[
                    ReqRef(ref=r.id, text=r.text, keywords=r.keywords)
                    for r in self.requirements_of([c.id for c in claims])
                ],
                language=self.lang,
            ),
        )

    async def generate_inventions(self, facts: Facts, fixed: list[CVElement]) -> None:
        sourced = self.pool.live(Slot.HIGHLIGHT, Slot.SKILL)
        els = fixed + [self.element(facts, c) for c in sourced]
        cov = score(els, self.jd.requirements, self.value, self.params, self.as_of)
        coverage = {r.requirement_id: r.coverage for r in cov.requirements}
        # Real evidence first: no invention for a requirement that a sourced candidate
        # already matches at the configured level (placement aside), so an invention
        # under a recent position cannot displace a real claim from an older one.
        floor = self.params.level_value(MatchLevel(self.s.optimizer.invent_below_match))
        matched = {
            r.id
            for r in self.jd.requirements
            for c in sourced
            if self.value(r.id, f"cand:{c.id}") >= floor - 1e-9
        }
        targets = [
            r
            for r in self.jd.requirements
            if r.category in INVENTABLE
            and coverage.get(r.id, 0.0) < 1 - 1e-9
            and r.id not in matched
        ]
        if not targets or not facts.positions:
            return
        prefs = {f"P{i}": p for i, p in enumerate(facts.positions, start=1)}
        claims = sorted(facts.claims.values(), key=lambda c: c.id)
        crefs = {f"C{i}": c for i, c in enumerate(claims, start=1)}
        positions = [
            PositionText(
                ref=r,
                text=f"{p.title} at {p.employer}, {p.start_month} to {p.end_month or 'current'}",
            )
            for r, p in prefs.items()
        ]
        claim_texts = [ClaimText(ref=r, text=c.text) for r, c in crefs.items()]
        outs = await asyncio.gather(
            *(
                self.runner.run(
                    GENERATE_INVENTIONS,
                    InventionsInput(
                        requirement=r.text,
                        category=r.category,
                        keywords=r.keywords,
                        positions=positions,
                        claims=claim_texts,
                        language=self.lang,
                    ),
                )
                for r in targets
            )
        )
        for r, out in zip(targets, outs, strict=True):
            for inv in out.candidates:
                slot = Slot.SKILL if inv.slot == "skill" else Slot.HIGHLIGHT
                pid = prefs[inv.position_ref].id if slot is Slot.HIGHLIGHT else None
                self.candidate(
                    facts,
                    slot,
                    inv.text,
                    [],
                    Fidelity.INVENTED,
                    pid,
                    inferred_from=sorted(crefs[x].id for x in inv.inferred_from),
                    assertion_key=r.id,
                    inferable=inv.inferable,
                )

    # --- step 5: verify -------------------------------------------------------------------

    async def verify(self, facts: Facts, cands: Sequence[Candidate], vocab: Vocabulary) -> None:
        todo = [
            c
            for c in cands
            if c.fidelity is not Fidelity.UNMODIFIED
            and c.fidelity is not Fidelity.INVENTED
            and c.id not in self.pool.classified
        ]
        batches = [todo[i : i + CLASSIFY_BATCH] for i in range(0, len(todo), CLASSIFY_BATCH)]
        outs = await asyncio.gather(
            *(
                self.runner.run(
                    CLASSIFY_FIDELITY,
                    FidelityInput(
                        items=[
                            FidelityItem(
                                ref=f"X{i}",
                                text=c.text,
                                sources=[facts.claims[s].text for s in c.source_claim_ids],
                            )
                            for i, c in enumerate(batch, start=1)
                        ]
                    ),
                )
                for batch in batches
            )
        )
        for batch, out in zip(batches, outs, strict=True):
            for v in out.verdicts:
                c = batch[int(v.ref[1:]) - 1]
                final = more_altered(self.pool.intended[c.id], Fidelity(v.fidelity))
                self.pool.classified[c.id] = v.reason
                self.pool.cands[c.id] = c.model_copy(update={"fidelity": final})
        for c in cands:
            c = self.pool.cands[c.id]
            sources = [facts.claims[s] for s in c.source_claim_ids]
            if c.source_claim_ids and c.fidelity is Fidelity.INVENTED:
                self.pool.discarded[c.id] = "classified invented although built from claims"
                continue
            if not passes_hard_facts(c.text, c.fidelity, sources, vocab):
                self.pool.discarded[c.id] = "hard-fact check failed"
                continue
            prov = statement_provability(
                c.fidelity,
                [s.provability for s in sources] or [Provability.NONE],
                inferable=self.pool.inferable[c.id],
            )
            self.pool.cands[c.id] = c.model_copy(update={"provability": prov})

    # --- step 6: judge ------------------------------------------------------------------

    def value(self, rid: str, eid: str) -> float:
        return self.values.get((rid, eid), 0.0)

    async def judge_candidates(
        self, facts: Facts, cands: Sequence[Candidate], fixed: list[CVElement]
    ) -> None:
        els = fixed + [self.element(facts, c) for c in cands]
        self.values.update(await self.judge.values(self.jd.requirements, els))

    # --- steps 7–8: selection, headline and summary --------------------------------------

    def budget(self, facts: Facts) -> float:
        lpp, cpl = self.s.layout.lines_per_page, self.s.layout.chars_per_line
        sections = 2 + bool(facts.education) + bool(facts.languages) + bool(facts.certificates)
        fixed = (
            4  # name, contact, spacing
            + 2.2 * (sections + 1)  # headings incl. skills
            + 1.6 * len(facts.positions)
            + 1.3 * (len(facts.education) + len(facts.certificates))
            + (1 if facts.languages else 0)
            + 3  # skill group names
        )
        reserved = 1 + math.ceil(self.s.optimizer.summary_max_sentences * 130 / cpl)
        return self.s.optimizer.page_budget * lpp - fixed - reserved

    def optimizer(self, facts: Facts, fixed: list[CVElement]) -> Optimizer:
        return Optimizer(
            self.jd.requirements,
            fixed,
            lambda c: self.element(facts, c),
            self.value,
            self.params,
            self.as_of,
        )

    def select(self, facts: Facts, fixed: list[CVElement]) -> Selection:
        opt = self.optimizer(facts, fixed)
        budget = self.budget(facts)
        p = SelectionParams(
            budget_lines=budget,
            exaggeration_min_gain_pp=self.s.optimizer.exaggeration_min_gain_pp,
            tie_window_pp=self.s.optimizer.tie_window_pp,
        )
        cands = [c for c in self.pool.live(Slot.HIGHLIGHT, Slot.SKILL) if c.id not in self.blocked]
        sel = opt.select(cands, p)
        fillers: dict[str, list[Candidate]] = {}
        for c in self.pool.cands.values():
            if (
                c.slot is Slot.HIGHLIGHT
                and c.source_claim_ids
                and c.id not in self.blocked
                and c.id not in self.pool.discarded
            ):
                fillers.setdefault(c.position_id or "", []).append(c)

        def relevance(c: Candidate) -> float:
            return max(
                (
                    self.relevance.get(s, {}).get(r.id, 0.0)
                    for s in c.source_claim_ids
                    for r in self.jd.requirements
                ),
                default=0.0,
            )

        return opt.fill(sel, fillers, [p.id for p in facts.positions], budget, relevance)

    async def summary_candidates(
        self, facts: Facts, sel: Selection, vocab: Vocabulary
    ) -> list[Candidate]:
        chosen = [c for c in sel.chosen if c.source_claim_ids]
        if not chosen:
            return []
        refs = {f"S{i}": c for i, c in enumerate(chosen, start=1)}
        out = await self.runner.run(
            WRITE_SUMMARY,
            SummaryInput(
                title=self.jd.title,
                statements=[ClaimText(ref=r, text=c.text) for r, c in refs.items()],
                requirements=[
                    ReqRef(ref=r.id, text=r.text, keywords=r.keywords)
                    for r in self.jd.requirements
                    if r.category is not C.LOGISTICS
                ],
                language=self.lang,
            ),
        )
        made = []
        for slot, items in ((Slot.HEADLINE, out.headlines), (Slot.SUMMARY, out.sentences)):
            for item in items:
                sources = sorted({s for r in item.source_refs for s in refs[r].source_claim_ids})
                made.append(
                    self.candidate(facts, slot, item.text, sources, Fidelity(item.fidelity), None)
                )
        await self.verify(facts, made, vocab)
        # Fallback for the minimum of 2 sentences: relevant claims, unmodified, those not
        # already selected as highlights first (used only if too few sentences survive).
        used = {s for c in sel.chosen if c.slot is Slot.HIGHLIGHT for s in c.source_claim_ids}
        claims = sorted(
            (
                c
                for c in facts.claims.values()
                if c.type is not ClaimType.SKILL and self.relevant(c.id)
            ),
            key=lambda c: (c.id in used, -max(self.relevance[c.id].values()), c.id),
        )
        for c in claims:
            fb = self.candidate(facts, Slot.SUMMARY, c.text, [c.id], Fidelity.UNMODIFIED, None)
            self.fallback.add(fb.id)
            made.append(fb)
        # Fallback headline: the most recent position's title, backed by its claims.
        for pos in facts.positions:
            backing = sorted(c.id for c in facts.claims.values() if c.position_id == pos.id)
            if backing:
                fb = self.candidate(
                    facts, Slot.HEADLINE, pos.title, backing, Fidelity.UNMODIFIED, None
                )
                self.fallback.add(fb.id)
                made.append(fb)
                break
        await self.verify(facts, made, vocab)
        return [self.pool.cands[c.id] for c in made if c.id not in self.pool.discarded]

    def choose_summary(
        self, facts: Facts, fixed: list[CVElement], sel: Selection, options: list[Candidate]
    ) -> Selection:
        """The best headline, then 2–4 summary sentences (at least 2, even at no gain)."""
        opt = self.optimizer(facts, fixed)
        min_gain = self.s.optimizer.exaggeration_min_gain_pp

        def pick(
            pool: list[Candidate], chosen: list[Candidate]
        ) -> tuple[Candidate, float, float] | None:
            base = opt.total(chosen)
            gains = {c.id: opt.total([*chosen, c]) - base for c in pool}
            best_plain: dict[frozenset[str], float] = {}
            for c in pool:
                if c.fidelity is not Fidelity.EXAGGERATED:
                    k = frozenset(c.source_claim_ids)
                    best_plain[k] = max(best_plain.get(k, 0.0), gains[c.id])
            ok = [
                c
                for c in pool
                if c.fidelity is not Fidelity.EXAGGERATED
                or gains[c.id] >= best_plain.get(frozenset(c.source_claim_ids), 0.0) + min_gain
            ]
            if not ok:
                return None
            top = max(gains[c.id] for c in ok)
            tied = [c for c in ok if gains[c.id] >= top - self.s.optimizer.tie_window_pp]
            best = min(tied, key=tie_key)
            return best, gains[best.id], best_plain.get(frozenset(best.source_claim_ids), 0.0)

        heads = [
            c
            for c in options
            if c.slot is Slot.HEADLINE and c.id not in self.blocked and c.id not in self.fallback
        ]
        head_fallback = [
            c
            for c in options
            if c.slot is Slot.HEADLINE and c.id in self.fallback and c.id not in self.blocked
        ]
        sentences = [
            c
            for c in options
            if c.slot is Slot.SUMMARY and c.id not in self.blocked and c.id not in self.fallback
        ]
        fallback = [
            c
            for c in options
            if c.slot is Slot.SUMMARY and c.id in self.fallback and c.id not in self.blocked
        ]
        chosen = list(sel.chosen)
        if heads and (got := pick(heads, chosen)):
            self._take(sel, chosen, *got)
        elif head_fallback:
            c = head_fallback[0]
            self._take(sel, chosen, c, opt.total([*chosen, c]) - opt.total(chosen), 0.0)
        picked = 0
        while sentences and picked < self.s.optimizer.summary_max_sentences:
            got = pick(sentences, chosen)
            if got is None or (got[1] <= 1e-9 and picked >= self.s.optimizer.summary_min_sentences):
                break
            self._take(sel, chosen, *got)
            sentences = [c for c in sentences if c.id != got[0].id]
            picked += 1
        while fallback and picked < self.s.optimizer.summary_min_sentences:
            c = fallback.pop(0)  # already in preference order
            self._take(sel, chosen, c, opt.total([*chosen, c]) - opt.total(chosen), 0.0)
            picked += 1
        return sel

    def _take(
        self, sel: Selection, chosen: list[Candidate], c: Candidate, gain: float, alt: float
    ) -> None:
        chosen.append(c)
        sel.chosen.append(c)
        sel.gains[c.id] = gain
        if c.fidelity is Fidelity.EXAGGERATED:
            sel.alternative_gain[c.id] = alt

    # --- step 9: assemble --------------------------------------------------------------

    async def group(self, keywords: list[str]) -> list[tuple[str, list[str]]]:
        if not keywords:
            return []
        if len(keywords) < 4:
            return [("", keywords)]
        out = await self.runner.run(GROUP_SKILLS, GroupInput(keywords=keywords, language=self.lang))
        return [(g.name, g.keywords) for g in out.groups]

    def fact_sections(self, facts: Facts) -> dict[str, Any]:
        """Education, languages and certificates exactly as stored."""
        education = []
        for e in facts.education:
            entry: dict[str, Any] = {"institution": e.institution}
            for key, value in (
                ("area", e.area),
                ("studyType", e.degree_type),
                ("startDate", e.start_month),
                ("endDate", e.end_month),
                ("score", e.grade),
            ):
                if value:
                    entry[key] = value
            education.append(entry)
        languages = []
        for lg in facts.languages:
            name, fluency = language_display(lg.language_code, lg.level, self.lang)
            languages.append({"language": name, "fluency": fluency})
        certificates = []
        for c in facts.certificates:
            entry = {"name": c.name}
            for key, value in (("date", c.issued_month), ("issuer", c.issuer), ("url", c.url)):
                if value:
                    entry[key] = value
            certificates.append(entry)
        return {"education": education, "languages": languages, "certificates": certificates}

    def basics(self, facts: Facts, headline: str, summary: list[str]) -> dict[str, Any]:
        p = facts.profile
        b: dict[str, Any] = {"name": p.name}
        if headline:
            b["label"] = headline
        for key, value in (("email", p.email), ("phone", p.phone), ("url", p.website)):
            if value:
                b[key] = value
        if summary:
            b["summary"] = " ".join(summary)
        loc: dict[str, str] = {}
        if p.city:
            loc["city"] = p.city
        if p.country:
            loc["countryCode" if len(p.country) == 2 else "region"] = p.country
        if loc:
            b["location"] = loc
        if p.profiles:
            b["profiles"] = [{"network": x.network, "url": x.url} for x in p.profiles]
        return b

    def work(self, facts: Facts, highlights: dict[str, list[Candidate]]) -> list[dict[str, Any]]:
        out = []
        for p in facts.positions:
            w: dict[str, Any] = {
                "name": p.employer,
                "position": p.title,
                "startDate": p.start_month,
            }
            if p.employer_location:
                w["location"] = p.employer_location
            if p.end_month:
                w["endDate"] = p.end_month
            w["highlights"] = [c.text for c in highlights.get(p.id, [])]
            out.append(w)
        return out

    async def assemble(
        self, facts: Facts, chosen: list[Candidate], impacts: dict[str, float]
    ) -> tuple[dict[str, Any], dict[tuple[str, int | None], Candidate]]:
        """cv.json and the candidate behind every statement pointer."""

        def rank(c: Candidate) -> tuple[float, str]:
            return (-impacts.get(c.id, 0.0), c.id)

        heads = [c for c in chosen if c.slot is Slot.HEADLINE]
        summary = sorted((c for c in chosen if c.slot is Slot.SUMMARY), key=rank)
        skills = sorted((c for c in chosen if c.slot is Slot.SKILL), key=rank)
        highlights: dict[str, list[Candidate]] = {}
        for c in sorted((c for c in chosen if c.slot is Slot.HIGHLIGHT), key=rank):
            highlights.setdefault(c.position_id or "", []).append(c)
        by_text = {c.text: c for c in skills}
        groups = await self.group([c.text for c in skills])
        cv: dict[str, Any] = {
            "basics": self.basics(facts, heads[0].text if heads else "", [c.text for c in summary])
        }
        cv["work"] = self.work(facts, highlights)
        facts_json = self.fact_sections(facts)
        cv["education"] = facts_json["education"]
        cv["skills"] = [
            {"name": name, "keywords": kws} if name else {"keywords": kws} for name, kws in groups
        ]
        cv["languages"] = facts_json["languages"]
        cv["certificates"] = facts_json["certificates"]
        cv = {k: v for k, v in cv.items() if v or k == "basics"}
        placed: dict[tuple[str, int | None], Candidate] = {}
        if heads:
            placed["/basics/label", None] = heads[0]
        for i, c in enumerate(summary):
            placed["/basics/summary", i] = c
        for i, p in enumerate(facts.positions):
            for j, c in enumerate(highlights.get(p.id, [])):
                placed[f"/work/{i}/highlights/{j}", None] = c
        for i, (_, kws) in enumerate(groups):
            for j, k in enumerate(kws):
                placed[f"/skills/{i}/keywords/{j}", None] = by_text[k]
        return cv, placed

    # --- the whole pipeline ------------------------------------------------------------

    async def run(self) -> None:
        facts = await self.load()
        self.facts = facts
        if not facts.claims:
            raise InputError("No confirmed claims: ingest and review documents first.")
        await self.translate(facts)
        vocab = self.vocabulary(facts)
        self.vocab = vocab
        fixed = self.fixed_elements(facts)
        self.fixed = fixed
        await self.judge_relevance(facts)
        await self.generate_candidates(facts)
        await self.verify(facts, list(self.pool.cands.values()), vocab)
        await self.judge_candidates(facts, self.pool.live(), fixed)
        await self.generate_inventions(facts, fixed)
        inventions = [c for c in self.pool.live() if c.fidelity is Fidelity.INVENTED]
        await self.verify(facts, inventions, vocab)
        await self.judge_candidates(
            facts, [c for c in inventions if c.id not in self.pool.discarded], fixed
        )
        fillers = [self.pool.cands[i] for i in self.pool.filler]
        await self.judge_candidates(facts, fillers, fixed)

        sel = self.select(facts, fixed)
        summary_options = await self.summary_candidates(facts, sel, vocab)
        await self.judge_candidates(facts, summary_options, fixed)

        for iteration in range(1, self.s.optimizer.max_render_iterations + 1):
            sel = self.select(facts, fixed)
            sel = self.choose_summary(facts, fixed, sel, summary_options)
            self.selection = sel
            done = await self.assemble_and_check(facts, sel)
            if done:
                log.info("generation done", extra={"iterations": iteration})
                return
        raise ValidationFailed(
            f"No CV satisfying every rule within {self.s.optimizer.page_budget} page(s) after "
            f"{self.s.optimizer.max_render_iterations} rounds."
        )

    def vocabulary(self, facts: Facts) -> Vocabulary:
        groups: list[list[str]] = []
        for r in self.jd.requirements:
            if r.category in (C.HARD_SKILL, C.CERTIFICATION):
                groups.append([*r.keywords, *r.synonyms])
        groups += [
            [k] for c in facts.claims.values() if c.type is ClaimType.SKILL for k in c.keywords
        ]
        groups += [[p.employer] for p in facts.positions]
        return Vocabulary(groups)

    def fixed_elements(self, facts: Facts) -> list[CVElement]:
        base = JSONResume.model_validate(self.fact_sections(facts))
        return [
            e
            for e in parse_elements(base, self.as_of, self.lang, self.placement)
            if e.kind in (ElementKind.EDUCATION, ElementKind.LANGUAGE, ElementKind.CERTIFICATE)
        ]

    async def assemble_and_check(self, facts: Facts, sel: Selection) -> bool:
        """Assemble, score, validate and render once. False after dropping a statement."""
        chosen = list(sel.chosen)
        draft, placed0 = await self.assemble(facts, chosen, {})
        first = await self.svc._scoring.score_cv(
            self.user, JSONResume.model_validate(draft), self.jd, self.as_of
        )
        impacts = {
            placed0[_split(eid)].id: v
            for eid, v in first.result.impacts.items()
            if _split(eid) in placed0
        }
        cv, placed = await self.assemble(facts, chosen, impacts)
        self.check_schema(cv)
        self.check_facts(facts, cv)
        scored = await self.svc._scoring.score_cv(
            self.user, JSONResume.model_validate(cv), self.jd, self.as_of
        )
        statements = {
            key: PlacedStatement(
                pointer=key[0],
                sentence=key[1],
                text=c.text,
                candidate=c,
                sources=[facts.claims[s] for s in c.source_claim_ids],
                inferable=self.pool.inferable[c.id],
                gain=sel.gains.get(c.id, 0.0),
                alternative_gain=sel.alternative_gain.get(c.id),
            )
            for key, c in placed.items()
        }
        report, rows = self.report(facts, scored.result, scored.elements, statements)
        annotations = self.annotations(placed, scored.result, rows)
        violations = validate_statements(
            list(statements.values()),
            annotations,
            self.vocab,
            self.s.optimizer.exaggeration_min_gain_pp,
        )
        if violations:
            v = violations[0]
            if not v.candidate_id:
                raise ValidationFailed(f"Internal error: {v.message}")
            self.drop(v.candidate_id, f"rule: {v.rule}")
            return False
        with tempfile.TemporaryDirectory() as tmp:
            model = build_render_model(JSONResume.model_validate(cv), self.s, lang=self.lang)
            pages = self.svc._render.render_model(model, Path(tmp) / "clean.pdf").pages
        if pages > self.s.optimizer.page_budget:
            droppable = [
                c
                for c in chosen
                if c.slot is not Slot.HEADLINE
                and not (
                    c.slot is Slot.SUMMARY
                    and sum(x.slot is Slot.SUMMARY for x in chosen)
                    <= self.s.optimizer.summary_min_sentences
                )
            ]
            if not droppable:
                raise ValidationFailed(
                    "The CV does not fit its page budget even without optional statements."
                )
            worst = min(droppable, key=lambda c: (impacts.get(c.id, 0.0), -len(c.text), c.id))
            self.drop(worst.id, "budget")
            return False
        self.cv, self.placed, self.final_score = cv, placed, scored.result
        self.final_elements, self.report_obj, self.annotations_list = (
            scored.elements,
            report,
            annotations,
        )
        self.statements = statements
        return True

    def drop(self, cid: str, why: str) -> None:
        self.blocked.add(cid)
        self.pool.discarded.setdefault(cid, why)
        log.info("statement dropped", extra={"candidate": cid, "reason": why})

    def check_schema(self, cv: dict[str, Any]) -> None:
        schema = json.loads(
            files("cvopt.resources").joinpath("jsonresume.schema.json").read_text(encoding="utf-8")
        )
        try:
            jsonschema.validate(cv, schema)
        except jsonschema.ValidationError as err:
            raise ValidationFailed(
                f"cv.json violates the JSON Resume schema: {err.message}"
            ) from err

    def check_facts(self, facts: Facts, cv: dict[str, Any]) -> None:
        """Fact records must come out exactly as stored; a mismatch is a bug."""
        work = cv.get("work", [])
        expected = [
            (p.employer, p.title, p.employer_location, p.start_month, p.end_month)
            for p in facts.positions
        ]
        got = [
            (w["name"], w["position"], w.get("location", ""), w["startDate"], w.get("endDate"))
            for w in work
        ]
        if got != expected:
            raise ValidationFailed("Internal error: positions differ from the database.")

    # --- annotations and report -------------------------------------------------------

    def annotations(
        self,
        placed: dict[tuple[str, int | None], Candidate],
        result: ScoreResult,
        rows: dict[str, int],
    ) -> list[Annotation]:
        wins: dict[str, list[str]] = {}
        for r in result.requirements:
            for eid in r.winners:
                wins.setdefault(eid, []).append(r.requirement_id)
        out = []
        for (pointer, sentence), c in placed.items():
            eid = pointer if sentence is None else f"{pointer}#{sentence}"
            out.append(
                Annotation(
                    pointer=pointer,
                    sentence=sentence,
                    text=c.text,
                    fidelity=c.fidelity,
                    provability=c.provability,
                    source_claim_ids=c.source_claim_ids,
                    inferred_from=c.inferred_from,
                    requirement_ids=wins.get(eid, []),
                    impact=result.impacts.get(eid, 0.0),
                    row=rows.get(eid, 0),
                )
            )
        return out

    def report(
        self,
        facts: Facts,
        result: ScoreResult,
        elements: list[CVElement],
        statements: dict[tuple[str, int | None], PlacedStatement],
    ) -> tuple[Report, dict[str, int]]:
        infos = {}
        for (pointer, sentence), p in statements.items():
            eid = pointer if sentence is None else f"{pointer}#{sentence}"
            infos[eid] = StatementInfo(
                fidelity=p.candidate.fidelity,
                provability=p.candidate.provability,
                source=" | ".join(facts.original_text[s] for s in p.candidate.source_claim_ids)
                or (
                    "inferred from: "
                    + " | ".join(facts.original_text[s] for s in p.candidate.inferred_from)
                    if p.candidate.inferred_from
                    else ""
                ),
            )
        fact_prov: dict[str, Provability] = {}
        for i, e in enumerate(facts.education):
            fact_prov[f"/education/{i}"] = facts.fact_provability[RecordType.EDUCATION, e.id]
        for i, lg in enumerate(facts.languages):
            fact_prov[f"/languages/{i}"] = facts.fact_provability[RecordType.LANGUAGE, lg.id]
        for i, c in enumerate(facts.certificates):
            fact_prov[f"/certificates/{i}"] = facts.fact_provability[RecordType.CERTIFICATE, c.id]
        used = {s for p in statements.values() for s in p.candidate.source_claim_ids}
        dropped = []
        for cid in sorted(facts.claims):
            if cid in used or not self.relevant(cid):
                continue
            reasons = {
                self.pool.discarded.get(x.id) or self.selection.reasons.get(x.id, "")
                for x in self.pool.cands.values()
                if cid in x.source_claim_ids
            }
            reason = (
                "budget"
                if "budget" in reasons
                else ("superseded" if "superseded" in reasons else "no gain")
            )
            dropped.append(
                DroppedClaim(
                    text=facts.original_text[cid],
                    reason=reason,
                    requirements=[r.id for r in self.requirements_of([cid])],
                )
            )
        ranges = [(p.start_month, p.end_month or f"{self.as_of:%Y-%m}") for p in facts.positions]
        return build_report(
            self.jd,
            result,
            elements,
            infos,
            fact_prov,
            dropped,
            ranges,
            self.as_of,
            self.s.report.timeline_gap_months,
        )

    # --- step 11: write ------------------------------------------------------------------

    def write(self, run_dir: Path, info: RunInfo) -> list[Path]:
        out: list[Path] = []

        def put(name: str, text: str) -> None:
            path = run_dir / name
            path.write_text(text, encoding="utf-8")
            out.append(path)

        put("cv.json", dump(self.cv))
        put("annotations.json", dump([a.model_dump(mode="json") for a in self.annotations_list]))
        put("report.json", dump(self.report_obj.model_dump(mode="json")))
        put("report.csv", report_csv(self.report_obj))
        put("candidates.json", dump(self.candidates_json()))
        model = build_render_model(
            JSONResume.model_validate(self.cv),
            self.s,
            lang=self.lang,
            annotations=self.annotations_list,
            report=self.report_obj,
        )
        out.append(self.svc._render.render_model(model, run_dir / "cv-annotated.pdf").path)
        versions = dict(info.prompt_versions)
        versions.update({t.name: t.version for t in TASKS})
        updated = info.model_copy(
            update={
                "prompt_versions": dict(sorted(versions.items())),
                "models": {"strong": self.s.llm.strong_model, "fast": self.s.llm.fast_model},
                "config": self.s.model_dump(mode="json"),
            }
        )
        put("run.json", dump(updated.model_dump(mode="json")))
        return out

    def candidates_json(self) -> list[dict[str, Any]]:
        chosen = {c.id for c in self.placed.values()}
        rows = []
        for c in self.pool.cands.values():
            judged = {
                r.id: round(self.value(r.id, f"cand:{c.id}"), 3)
                for r in self.jd.requirements
                if self.value(r.id, f"cand:{c.id}") > 0
            }
            why = (
                "selected"
                if c.id in chosen
                else self.pool.discarded.get(c.id)
                or self.selection.reasons.get(c.id)
                or ("filler only" if c.id in self.pool.filler else "not selected")
            )
            rows.append(
                {
                    **c.model_dump(mode="json"),
                    "intended_fidelity": self.pool.intended[c.id].value,
                    "classifier_reason": self.pool.classified.get(c.id, ""),
                    "judgements": judged,
                    "status": why,
                    "inferable": self.pool.inferable[c.id],
                    "gain": self.selection.gains.get(c.id),
                    "alternative_gain": self.selection.alternative_gain.get(c.id),
                }
            )
        return rows


def _split(eid: str) -> tuple[str, int | None]:
    if "#" in eid:
        pointer, n = eid.split("#")
        return pointer, int(n)
    return eid, None
