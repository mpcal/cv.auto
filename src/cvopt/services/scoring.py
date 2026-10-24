"""ScoringService (F5): score any JSON Resume file against a requirement list."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import CVElement, JSONResume, Placement, parse_elements
from cvopt.domain.jd import JobDescription
from cvopt.domain.report import Report, StatementInfo, build_report
from cvopt.domain.run import RunInfo
from cvopt.domain.scoring import ScoreResult, ScoringParams, score
from cvopt.domain.text import normalize
from cvopt.errors import InputError
from cvopt.llm.judge import Judge
from cvopt.llm.runner import TaskRunnerFactory
from cvopt.ports import Clock


@dataclass(frozen=True)
class Scored:
    result: ScoreResult
    elements: list[CVElement]
    jd: JobDescription
    as_of: date


def scoring_params(s: Settings) -> ScoringParams:
    c = s.scoring
    return ScoringParams(
        weight_must=c.weight_must,
        weight_nice=c.weight_nice,
        match_exact=c.match_exact,
        match_synonym=c.match_synonym,
        match_semantic=c.match_semantic,
        match_partial=c.match_partial,
    )


def placement(s: Settings) -> Placement:
    c = s.scoring
    return Placement(
        recent_highlight=c.placement_recent_highlight,
        older_highlight=c.placement_older_highlight,
        headline_summary=c.placement_headline_summary,
        skills=c.placement_skills,
        recency_years=c.recency_years,
    )


def read_json(path: Path, what: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise InputError(f"Cannot read {what} {path}: {err}") from err


def load_cv(path: Path) -> JSONResume:
    try:
        return JSONResume.model_validate(read_json(path, "CV"))
    except ValidationError as err:
        raise InputError(f"{path} is not a JSON Resume file: {err}") from err


def load_requirements(path: Path) -> JobDescription:
    try:
        return JobDescription.model_validate(read_json(path, "requirements"))
    except ValidationError as err:
        raise InputError(f"{path} is not a requirement list: {err}") from err


class ScoringService:
    def __init__(self, runners: TaskRunnerFactory, clock: Clock, settings: Settings) -> None:
        self._runners = runners
        self._clock = clock
        self._settings = settings

    def as_of(self, cv_path: Path, explicit: date | None) -> date:
        """From run.json next to the CV, else the explicit date, else today (P2 §7.4)."""
        run_json = cv_path.parent / "run.json"
        if run_json.is_file():
            try:
                return RunInfo.model_validate(read_json(run_json, "run.json")).as_of
            except ValidationError as err:
                raise InputError(f"{run_json} is invalid: {err}") from err
        return explicit or self._clock.today()

    async def score_cv(
        self, user: UserContext, cv: JSONResume, jd: JobDescription, as_of: date
    ) -> Scored:
        elements = parse_elements(cv, as_of, jd.language, placement(self._settings))
        params = scoring_params(self._settings)
        judge = Judge(
            self._runners.for_user(user), params, self._settings.matching.judge_batch_size
        )
        values = await judge.values(jd.requirements, elements)
        result = score(
            elements, jd.requirements, lambda r, e: values.get((r, e), 0.0), params, as_of
        )
        return Scored(result, elements, jd, as_of)

    async def score(
        self,
        user: UserContext,
        cv_path: Path,
        requirements_path: Path,
        as_of: date | None = None,
    ) -> Scored:
        cv = load_cv(cv_path)
        jd = load_requirements(requirements_path)
        return await self.score_cv(user, cv, jd, self.as_of(cv_path, as_of))


def standalone_report(
    scored: Scored, annotations: list[Annotation] | None, gap_months: int
) -> Report:
    """The report of a CV scored on its own (F5): no dropped rows; a statement whose text
    no longer matches its annotation gets blank fidelity and provability."""
    by_key = {(a.pointer, a.sentence): a for a in annotations or []}
    infos: dict[str, StatementInfo] = {}
    for e in scored.elements:
        pointer, _, n = e.id.partition("#")
        a = by_key.get((pointer, int(n) if n else None))
        if a is not None and normalize(a.text) == normalize(e.text):
            infos[e.id] = StatementInfo(
                fidelity=a.fidelity, provability=a.provability, source=", ".join(a.source_claim_ids)
            )
    ranges = sorted(
        {
            (e.start_month, e.end_month or f"{scored.as_of:%Y-%m}")
            for e in scored.elements
            if e.start_month
        }
    )
    report, _ = build_report(
        scored.jd, scored.result, scored.elements, infos, {}, [], ranges, scored.as_of, gap_months
    )
    return report
