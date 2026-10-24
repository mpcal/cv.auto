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
        path = self._root / "users" / user.user_key
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _subdir(self, user: UserContext, name: str) -> Path:
        path = self.user_dir(user) / name
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
        path = self._subdir(user, "runs") / name
        path.mkdir(mode=0o700)
        return path

    def run_dir(self, user: UserContext, ref: str) -> Path:
        runs = self._subdir(user, "runs")
        if (runs / ref).is_dir():
            return runs / ref
        matches = [p for p in runs.iterdir() if p.is_dir() and p.name.startswith(ref)]
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
