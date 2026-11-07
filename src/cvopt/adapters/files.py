"""LocalFileStore: per-user directories under <data_root>/users/<user-key>/."""

import shutil
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from cvopt.context import UserContext
from cvopt.errors import ConflictError, InputError


class LocalFileStore:
    def __init__(self, data_root: Path) -> None:
        self._root = data_root

    def user_dir(self, user: UserContext) -> Path:
        # The configured data root may itself be a symlink. Partition boundaries
        # beneath it must remain real directories before any read or creation.
        root = self._root.resolve()
        users = root / "users"
        self._real_directory(users)
        path = users / user.user_key
        self._real_directory(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    @staticmethod
    def _real_directory(path: Path) -> None:
        if path.is_symlink():
            raise InputError("User storage directories cannot be redirected by symlinks.")
        if path.exists() and not path.is_dir():
            raise InputError("User storage requires real directories.")

    def runs_dir(self, user: UserContext) -> Path:
        return self._subdir(user, "runs")

    def _subdir(self, user: UserContext, name: str) -> Path:
        path = self.user_dir(user) / name
        self._real_directory(path)
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def save_document(self, user: UserContext, document_id: str, ext: str, data: bytes) -> Path:
        path = self.document_path(user, document_id, ext)
        path.touch(mode=0o600)
        path.write_bytes(data)
        return path

    def document_path(self, user: UserContext, document_id: str, ext: str) -> Path:
        return self._subdir(user, "documents") / f"{document_id}.{ext.lstrip('.')}"

    def new_run_dir(self, user: UserContext, name: str) -> Path:
        if not name or Path(name).name != name or name in (".", ".."):
            raise InputError("Run names must name a direct child of your runs directory.")
        path = self.runs_dir(user) / name
        path.mkdir(mode=0o700)
        return path.resolve()

    def run_dir(self, user: UserContext, ref: str) -> Path:
        runs = self.runs_dir(user).resolve()
        supplied = Path(ref)
        if (
            not ref
            or ".." in supplied.parts
            or (not supplied.is_absolute() and supplied.name != ref)
        ):
            raise InputError("Run must be an owned run name, prefix or absolute path.")
        candidate = supplied if supplied.is_absolute() else runs / supplied
        resolved = candidate.resolve()
        if resolved.parent != runs:
            raise InputError("Run must be a direct child of your runs directory.")
        if resolved.is_dir():
            return resolved
        matches = [
            p.resolve()
            for p in runs.iterdir()
            if p.name.startswith(ref) and p.is_dir() and p.resolve().parent == runs
        ]
        if len(matches) != 1:
            raise InputError(f"Unknown run: {ref}" if not matches else f"Ambiguous run: {ref}")
        return matches[0]

    def lock(self, user: UserContext) -> AbstractContextManager[None]:
        return self._lock(self.user_dir(user) / ".lock")

    @contextmanager
    def _lock(self, path: Path) -> Iterator[None]:
        try:
            with FileLock(path, timeout=0):
                yield
        except Timeout as err:
            raise ConflictError("Another cvopt process is writing this user's data.") from err

    def delete_user_files(self, user: UserContext) -> None:
        shutil.rmtree(self._root / "users" / user.user_key, ignore_errors=True)
