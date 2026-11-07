"""Compare edited role requirements against one current owned profile snapshot."""

from datetime import date
from pathlib import Path
from typing import Any

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.comparison import (
    ComparedRole,
    Comparison,
    ComparisonRequirement,
    SavedTarget,
    recurring_gaps,
)
from cvopt.domain.enums import Importance
from cvopt.domain.provenance import content_fingerprint
from cvopt.domain.report import build_report
from cvopt.domain.scoring import score
from cvopt.errors import InputError
from cvopt.llm.judge import PAIR_TASK, pair_cache_version
from cvopt.llm.runner import TaskRunnerFactory
from cvopt.llm.tasks.judge_match import JUDGE_MATCH
from cvopt.llm.tasks.parse_jd import PARSE_JD
from cvopt.ports import Clock, RepositoryFactory
from cvopt.services.discovery import RunDiscoveryService, RunSummary
from cvopt.services.generation import TASKS
from cvopt.services.profile import ProfileCoverageService
from cvopt.services.reports import explain_report
from cvopt.services.scoring import load_requirements, read_json, scoring_context, scoring_params

CONTEXT_SECTIONS = ("scoring", "optimizer", "layout", "render")


def generation_context(settings: Settings) -> dict[str, Any]:
    versions = {task.name: task.version for task in [*TASKS, JUDGE_MATCH, PARSE_JD]}
    versions[PAIR_TASK] = pair_cache_version()
    return {
        **{key: getattr(settings, key).model_dump(mode="json") for key in CONTEXT_SECTIONS},
        "models": {"strong": settings.llm.strong_model, "fast": settings.llm.fast_model},
        "provider": settings.llm.provider,
        "prompt_versions": versions,
    }


def _json_artifact(summary: RunSummary, key: str) -> dict[str, Any]:
    path = summary.artifacts.get(key)
    if path is None:
        return {}
    try:
        value = read_json(Path(path), key)
    except InputError:
        return {}
    return value if isinstance(value, dict) else {}


def _config_stale(metadata: dict[str, Any], current: dict[str, Any]) -> bool | None:
    """A known difference is stale; missing identity alone stays unknown."""
    config = metadata.get("config")
    config = config if isinstance(config, dict) else {}
    llm = config.get("llm")
    saved = {
        **{key: config[key] for key in CONTEXT_SECTIONS if key in config},
        "provider": llm.get("provider") if isinstance(llm, dict) else None,
        "models": metadata.get("models"),
        "prompt_versions": metadata.get("prompt_versions"),
    }

    def compare(known: Any, expected: Any) -> bool | None:
        if isinstance(expected, dict):
            if not isinstance(known, dict):
                return None
            checks = [
                compare(known[key], value) if key in known else None
                for key, value in expected.items()
            ]
            if any(value is True for value in checks):
                return True
            return None if any(value is None for value in checks) else False
        if isinstance(expected, str) and not isinstance(known, str):
            return None
        return bool(known != expected)

    return compare(saved, current)


class ComparisonService:
    def __init__(
        self,
        discovery: RunDiscoveryService,
        repos: RepositoryFactory,
        runners: TaskRunnerFactory,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._discovery, self._runners = discovery, runners
        self._clock, self._settings = clock, settings
        self._profiles = ProfileCoverageService(repos, runners, settings)

    async def compare(
        self, user: UserContext, refs: list[str], *, as_of: date | None = None
    ) -> Comparison:
        if len(refs) < 2:
            raise InputError("Comparison requires at least two distinct owned runs.")
        common_date = as_of or self._clock.today()
        with self._runners.operation(user, "comparison") as operation:
            with operation.stage("load"):
                selected = [self._discovery.show(user, ref) for ref in refs]
                if len({row.path for row in selected}) != len(selected):
                    raise InputError(
                        "Comparison requires distinct runs; aliases name the same run."
                    )
                jobs = []
                for row in selected:
                    path = row.artifacts.get("requirements.json")
                    if path is None:
                        raise InputError(f"Run {row.name} has no owned editable requirements.")
                    jobs.append(load_requirements(Path(path)))
                facts = await self._profiles.load(user)
            assert facts.snapshot is not None
            roles = []
            effective = generation_context(self._settings)
            for saved, jd in zip(selected, jobs, strict=True):
                with operation.stage("profile_coverage"):
                    profile = await self._profiles.coverage(user, jd, common_date, facts=facts)
                metadata = _json_artifact(saved, "run.json")
                report = _json_artifact(saved, "report.json")
                stale: dict[str, bool | None] = {
                    "profile": report["profile_snapshot_fingerprint"] != facts.snapshot.fingerprint
                    if report.get("profile_snapshot_fingerprint")
                    else None,
                    "requirements": report["requirements_fingerprint"] != content_fingerprint(jd)
                    if report.get("requirements_fingerprint")
                    else None,
                    "config": _config_stale(metadata, effective),
                    "date": metadata["as_of"] != common_date.isoformat()
                    if metadata.get("as_of")
                    else None,
                }
                params = scoring_params(self._settings)
                empty = score([], jd.requirements, lambda r, e: 0, params, common_date)
                base, _ = build_report(jd, empty, [], {}, {}, [], [], common_date, 0)
                explained = explain_report(base, jd, profile, empty, [], {}, [], [])
                explanations = {r.requirement_id: r for r in explained.requirements}
                requirements = {r.id: r for r in jd.requirements}
                run_id = metadata.get("run_id")
                roles.append(
                    ComparedRole(
                        run_id=run_id if isinstance(run_id, str) and run_id.strip() else saved.name,
                        name=saved.name,
                        title=jd.title,
                        company=jd.company,
                        requirements_fingerprint=content_fingerprint(jd),
                        profile_supported_score=profile.result.total,
                        must_gap_points=sum(
                            r.gap_points
                            for r in profile.result.requirements
                            if requirements[r.requirement_id].importance is Importance.MUST
                        ),
                        saved_target=SavedTarget(
                            score=saved.target_score, generated_at=saved.published_at, stale=stale
                        ),
                        requirements=[
                            ComparisonRequirement(
                                requirement=requirements[r.requirement_id],
                                coverage=r.coverage,
                                weighted_missing_points=r.gap_points,
                                supporting_elements=explanations[
                                    r.requirement_id
                                ].supporting_elements,
                            )
                            for r in profile.result.requirements
                        ],
                    )
                )
            roles.sort(
                key=lambda r: (-r.profile_supported_score, r.must_gap_points, r.run_id, r.name)
            )
        return Comparison(
            as_of=common_date,
            profile_snapshot_fingerprint=facts.snapshot.fingerprint,
            profile_snapshot=facts.snapshot,
            scoring_context=scoring_context(self._settings, common_date),
            roles=roles,
            recurring_gaps=recurring_gaps(roles),
            usage=operation.summary(),
        )
