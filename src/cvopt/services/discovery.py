"""Read-only, user-scoped discovery of current and legacy run publications."""

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cvopt.context import UserContext
from cvopt.errors import InputError
from cvopt.ports import FileStore
from cvopt.services.publication import ALIASES, owned_artifact, selected_generation


class RunSummary(BaseModel):
    name: str
    path: Path
    status: Literal["new", "failed", "published"]
    published: bool
    publication_status: Literal["unpublished", "legacy", "published"]
    selected_generation_id: str | None = None
    created_at: str | None = None
    published_at: str | None = None
    last_attempt_at: str | None = None
    last_attempt: dict[str, Any] | None = None
    attempt_diagnostics: Literal["available", "incomplete", "unavailable"] = "unavailable"
    target_score: float | None = None
    profile_supported_score: float | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None


def _owned(path: Path, run: Path) -> Path | None:
    try:
        resolved = owned_artifact(path, run)
        return resolved if resolved.is_file() else None
    except (InputError, OSError):
        return None


def _read(path: Path, run: Path) -> dict[str, Any]:
    owned = _owned(path, run)
    if owned is None:
        return {}
    try:
        value = json.loads(owned.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _score(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        else None
    )


def _attempt(value: dict[str, Any]) -> dict[str, Any] | None:
    if value.get("status") not in ("running", "succeeded", "failed", "published_with_error"):
        return None
    result: dict[str, Any] = {
        k: _text(value.get(k)) for k in ("generation_id", "status", "started_at", "finished_at")
    }
    result["published"] = value.get("published") is True
    error = value.get("error_type")
    result["error_type"] = (
        error if error in ("provider", "validation", "input", "internal") else None
    )
    # Generated usage has no source text; legacy attempt records lack it.
    if isinstance(value.get("usage"), dict):
        result["usage"] = value["usage"]
    return result


class RunDiscoveryService:
    def __init__(self, files: FileStore) -> None:
        self._files = files

    def list(self, user: UserContext) -> list[RunSummary]:
        runs = self._files.runs_dir(user)
        if not runs.is_dir():
            return []
        result = []
        for path in sorted(runs.iterdir(), key=lambda p: p.name, reverse=True):
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                result.append(self.show(user, path.name))
            except InputError:
                continue
        return result

    def show(self, user: UserContext, ref: str) -> RunSummary:
        self._files.runs_dir(user)
        run = self._files.run_dir(user, ref)
        current = selected_generation(run)
        directory = current or run
        metadata = _read(directory / "run.json", run)
        initial = _read(run / "initial-run.json", run)
        report = _read(directory / "report.json", run)
        attempt = _attempt(_read(run / "last-attempt.json", run))
        artifacts = {}
        for alias, name in ALIASES.items():
            if (owned := _owned(directory / name, run)) is not None:
                artifacts[alias] = str(owned)
        for name in ("requirements.json", "jd.txt", "last-attempt.json", "intake-usage.json"):
            if (owned := _owned(run / name, run)) is not None:
                artifacts[name] = str(owned)
        published = _owned(directory / "cv.json", run) is not None
        legacy = current is None or current.name.startswith("legacy-")
        usage = _read(directory / "usage.json", run)
        return RunSummary(
            name=run.name,
            path=run,
            status="published"
            if published
            else "failed"
            if attempt and attempt["status"] == "failed"
            else "new",
            published=published,
            publication_status=("legacy" if legacy else "published")
            if published
            else "unpublished",
            selected_generation_id=current.name if current else None,
            created_at=_text(metadata.get("created_at")) or _text(initial.get("created_at")),
            published_at=_text(metadata.get("generated_at")) if published else None,
            last_attempt_at=(attempt["finished_at"] or attempt["started_at"]) if attempt else None,
            last_attempt=attempt,
            attempt_diagnostics=("incomplete" if attempt["status"] == "running" else "available")
            if attempt
            else "unavailable",
            target_score=_score(report.get("total")) if published else None,
            profile_supported_score=_score(report.get("profile_supported_score"))
            if published
            else None,
            artifacts=artifacts,
            usage=usage if usage.get("schema_version") == 2 else None,
        )
