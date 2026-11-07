"""Stage complete run artifacts and publish them through one atomic pointer.

requirements.json is editable input; requirements-snapshot.json is an output alias.
A caller pins once before reading or writing several artifacts of a publication.
"""

import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
from ulid import ULID

from cvopt.errors import InputError, LLMTaskError, ValidationFailed

POINTER = "current-generation"
ARTIFACTS = (
    "cv.json",
    "cv.pdf",
    "cv-annotated.pdf",
    "annotations.json",
    "report.json",
    "report.csv",
    "candidates.json",
    "run.json",
    "profile-snapshot.json",
    "usage.json",
)
REQUIRED = (*ARTIFACTS, "requirements.json")
ALIASES = {name: name for name in (*ARTIFACTS, "score-report.json", "score-report.csv")}
ALIASES["requirements-snapshot.json"] = "requirements.json"


def _json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _link(target: str, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}-{ULID()}")
    try:
        temporary.symlink_to(target)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _current(run: Path) -> Path | None:
    pointer = run / POINTER
    if not pointer.is_symlink():
        if pointer.exists():
            raise InputError("The run's current-generation must be a generation symlink.")
        return None
    try:
        current = pointer.resolve()
        generations = run / ".generations"
        invalid = (
            generations.is_symlink()
            or current.parent != generations.resolve()
            or not current.is_dir()
        )
    except (OSError, RuntimeError) as err:
        raise InputError("Cannot resolve the run's current-generation safely.") from err
    if invalid:
        raise InputError("The run's current-generation points outside its generations.")
    return current


def selected_generation(run: Path) -> Path | None:
    """Validate and return the selected generation, including metadata-only adoption."""
    return _current(run)


def owned_artifact(path: Path, run: Path) -> Path:
    """Resolve a run-owned artifact before accessing it; missing local files are allowed.

    The caller must already own the run. Internal publication aliases and configured
    root aliases are valid, but file symlinks cannot authorize another run's data.
    Explicit standalone render/score inputs use pin_paths instead of this policy.
    """
    try:
        resolved = path.resolve()
        if resolved.is_relative_to(run.resolve()):
            return resolved
    except (OSError, RuntimeError) as err:
        raise InputError("Cannot resolve a run artifact safely; check its file links.") from err
    raise InputError("A run artifact points outside its run; replace it with an owned file.")


def pin_paths(anchor: Path, *paths: Path) -> tuple[Path, ...]:
    """Pin inputs AND outputs to anchor's generation, including requirement snapshots.

    Unpublished/standalone files remain ordinary paths. An explicit requirement input
    beside a published CV means that CV's snapshot; generation reads editable input
    separately and does not use this helper for it.
    """
    absolute_anchor = Path(os.path.abspath(anchor))
    if absolute_anchor.is_symlink():
        target = Path(os.path.abspath(absolute_anchor.parent / os.readlink(absolute_anchor)))
        if target.parent == absolute_anchor.parent / POINTER:
            _current(absolute_anchor.parent)  # validate owned pointer aliases before resolving
    resolved_anchor = absolute_anchor.resolve()
    pinned: Path | None = None
    if resolved_anchor.parent.parent.name == ".generations":
        pinned = resolved_anchor.parent
    run = pinned.parent.parent if pinned is not None else resolved_anchor.parent
    result = [resolved_anchor]
    for path in paths:
        absolute = Path(os.path.abspath(path))
        if pinned is not None and absolute.parent.resolve() in (run, pinned):
            name = ALIASES.get(path.name, path.name)
            result.append(pinned / name)
        else:
            result.append(path.resolve())
    return tuple(result)


@dataclass
class GenerationStage:
    publication: "RunPublication"
    path: Path
    published: bool = False

    def publish(self) -> None:
        """Validate the complete core artifact set before any visible pointer switch."""
        missing = [name for name in REQUIRED if not (self.path / name).is_file()]
        if missing:
            raise ValidationFailed("Generation is incomplete: " + ", ".join(missing))
        for name in ("cv.pdf", "cv-annotated.pdf"):
            try:
                if not PdfReader(self.path / name).pages:
                    raise ValueError("empty PDF")
            except Exception as err:
                raise ValidationFailed(f"Generation contains an invalid {name}.") from err
        self.publication._publish(self.path)
        self.published = True


class RunPublication:
    """Run-local publication lifecycle; staging accepts additional Task 4/5 artifacts."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir.resolve()
        self.generations = self.run_dir / ".generations"
        if self.generations.is_symlink():
            raise InputError("The run's generations directory cannot be a symlink.")
        self.generations.mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def attempt(
        self, *, usage: Callable[[], dict[str, object]] | None = None
    ) -> Iterator[GenerationStage]:
        """Record safe diagnostics separately; pointer selection is the commit boundary.

        Post-commit errors propagate with status published_with_error when writable.
        A diagnostic write failure cannot roll back or delete committed artifacts.
        """
        generation_id = str(ULID())
        path = self.generations / generation_id
        path.mkdir(mode=0o700)
        stage = GenerationStage(self, path)
        status: dict[str, object] = {
            "generation_id": generation_id,
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "error_type": None,
            "published": False,
        }

        def record() -> None:
            if usage is not None:
                summary = usage()
                summary.update(status=status["status"], finished_at=status["finished_at"])
                status["usage"] = summary
            self._status(status)

        try:
            record()
            yield stage
            if not stage.published:
                raise ValidationFailed("Generation was not published.")
            status.update(
                status="succeeded", finished_at=datetime.now(UTC).isoformat(), published=True
            )
            record()
        except BaseException as err:
            # Pointer selection commits the generation, even if publish() did not
            # return or a later status write/body operation failed. Never remove it.
            committed = stage.published or (self.run_dir / POINTER).resolve() == path
            stage.published = committed
            status.update(
                status="published_with_error" if committed else "failed",
                published=committed,
                finished_at=datetime.now(UTC).isoformat(),
                error_type=(
                    "provider"
                    if isinstance(err, LLMTaskError)
                    else "validation"
                    if isinstance(err, ValidationFailed)
                    else "input"
                    if isinstance(err, InputError)
                    else "internal"
                ),
            )
            # Preserve the original error if storage also rejects diagnostics;
            # the selected pointer remains the publication truth.
            with suppress(OSError):
                record()
            if not committed:
                shutil.rmtree(path, ignore_errors=True)
            raise

    def _status(self, status: dict[str, object]) -> None:
        temporary = self.run_dir / f".attempt-{ULID()}.json"
        try:
            _json(temporary, status)
            os.replace(temporary, self.run_dir / "last-attempt.json")
        finally:
            temporary.unlink(missing_ok=True)

    def _adopt(self) -> None:
        """Copy legacy artifacts before replacing their paths with pointer aliases.

        Metadata-only initial runs are also adopted so run.json stays readable if a
        first publication fails while its aliases are being installed.
        """
        if _current(self.run_dir) is not None:
            return
        # Validate the entire source set, including missing/dangling links, before
        # creating initial metadata, copying artifacts or converting legacy paths.
        sources = {
            alias: owned_artifact(self.run_dir / alias, self.run_dir)
            for alias in (*ALIASES, "requirements.json", "initial-run.json")
        }
        initial = sources["initial-run.json"]
        if not initial.exists():
            shutil.copy2(sources["run.json"], initial)
        legacy = self.generations / f"legacy-{ULID()}"
        legacy.mkdir(mode=0o700)
        for alias, name in {**ALIASES, "requirements.json": "requirements.json"}.items():
            source = sources[alias]
            if source.is_file():
                shutil.copy2(source, legacy / name)
        _link(str(legacy.relative_to(self.run_dir)), self.run_dir / POINTER)

    def _publish(self, path: Path) -> None:
        self._adopt()
        aliases = {**ALIASES, **{p.name: p.name for p in path.iterdir() if p.is_file()}}
        aliases.pop("requirements.json", None)
        for alias, name in aliases.items():
            destination = self.run_dir / alias
            target = f"{POINTER}/{name}"
            if destination.is_symlink() and os.readlink(destination) == target:
                continue
            _link(target, destination)
        _link(str(path.relative_to(self.run_dir)), self.run_dir / POINTER)
