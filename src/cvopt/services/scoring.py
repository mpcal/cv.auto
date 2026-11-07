"""ScoringService (F5): score any JSON Resume file against a requirement list."""

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import CVElement, JSONResume, Placement, parse_elements
from cvopt.domain.jd import JobDescription
from cvopt.domain.provenance import ProfileSnapshot, content_fingerprint, snapshot_fingerprint
from cvopt.domain.report import Report, StatementInfo, build_report
from cvopt.domain.run import RunInfo
from cvopt.domain.scoring import ScoreResult, ScoringParams, score
from cvopt.domain.text import normalize
from cvopt.errors import InputError
from cvopt.llm.judge import Judge, pair_cache_version
from cvopt.llm.runner import TaskRunnerFactory
from cvopt.ports import Clock
from cvopt.services.publication import pin_paths


@dataclass(frozen=True)
class Scored:
    result: ScoreResult
    elements: list[CVElement]
    jd: JobDescription
    as_of: date
    values: dict[tuple[str, str], float] = field(default_factory=dict)
    matching_report: Report | None = None


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
        return Scored(result, elements, jd, as_of, values)

    async def score(
        self,
        user: UserContext,
        cv_path: Path,
        requirements_path: Path,
        as_of: date | None = None,
    ) -> Scored:
        cv_path, requirements_path = pin_paths(cv_path, requirements_path)
        cv = load_cv(cv_path)
        jd = load_requirements(requirements_path)
        scored = await self.score_cv(user, cv, jd, self.as_of(cv_path, as_of))
        matching = self._matching_report(cv_path.parent, cv, jd, scored.as_of)
        if matching is not None and not target_results_match(scored, matching):
            matching = None
        return Scored(
            scored.result, scored.elements, scored.jd, scored.as_of, scored.values, matching
        )

    def _matching_report(
        self, directory: Path, cv: JSONResume, jd: JobDescription, as_of: date
    ) -> Report | None:
        report_path, snapshot_path = directory / "report.json", directory / "profile-snapshot.json"
        if not report_path.is_file() or not snapshot_path.is_file():
            return None
        try:
            report = Report.model_validate(read_json(report_path, "report"))
            snapshot = ProfileSnapshot.model_validate(read_json(snapshot_path, "profile snapshot"))
        except (ValidationError, InputError):
            return None
        if (
            snapshot.schema_version != 2
            or not snapshot.fingerprint
            or snapshot.fingerprint != snapshot_fingerprint(snapshot)
            or report.profile_snapshot_fingerprint != snapshot.fingerprint
            or report.target_cv_fingerprint != content_fingerprint(cv)
            or report.requirements_fingerprint != content_fingerprint(jd)
            or report.scoring_context != scoring_context(self._settings, as_of)
        ):
            return None
        return report


def target_results_match(scored: Scored, historical: Report) -> bool:
    """Reuse profile explanations only when fresh target coverage and relations agree.

    File/config identity cannot establish stable semantic verdicts after cache loss.
    A changed value, positive relation or winning share makes support unavailable;
    the freshly computed target report remains authoritative.
    """

    def equal(first: float, second: float) -> bool:
        return math.isclose(first, second, rel_tol=1e-12, abs_tol=1e-9)

    if not equal(scored.result.total, historical.total):
        return False
    previous = {r.requirement_id: r for r in historical.requirements}
    if set(previous) != {r.requirement_id for r in scored.result.requirements}:
        return False
    for result in scored.result.requirements:
        old = previous[result.requirement_id]
        if not equal(result.coverage, old.target_coverage):
            return False
        current = {
            eid: value
            for (rid, eid), value in scored.values.items()
            if rid == result.requirement_id and value > 0
        }
        old_values = {hit.element_id: hit.value for hit in old.target_statements}
        if set(current) != set(old_values) or any(
            not equal(value, old_values[eid]) for eid, value in current.items()
        ):
            return False
        old_winners = {
            hit.element_id: hit.winning_share
            for hit in old.target_statements
            if hit.winning_share > 0
        }
        if set(result.winners) != set(old_winners) or any(
            not equal(value, old_winners[eid]) for eid, value in result.winners.items()
        ):
            return False
    return True


def scoring_context(settings: Settings, as_of: date) -> dict[str, object]:
    return {
        "as_of": as_of.isoformat(),
        "scoring": settings.scoring.model_dump(mode="json"),
        "models": {"strong": settings.llm.strong_model, "fast": settings.llm.fast_model},
        "judge_match_pair": pair_cache_version(),
    }


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
        scored.jd,
        scored.result,
        scored.elements,
        infos,
        {},
        [],
        ranges,
        scored.as_of,
        gap_months,
        scored.values,
    )
    if scored.matching_report is not None and target_results_match(scored, scored.matching_report):
        historical = scored.matching_report
        fields = {
            name: getattr(historical, name)
            for name in (
                "profile_supported_score",
                "target_minus_profile",
                "profile_snapshot_fingerprint",
                "target_cv_fingerprint",
                "requirements_fingerprint",
                "scoring_context",
                "requirements",
                "provenance",
                "profile_assertions",
            )
        }
        report = report.model_copy(update=fields)
    return report
