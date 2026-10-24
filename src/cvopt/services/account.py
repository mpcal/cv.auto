"""AccountService (F8): export all of a user's data, or delete it."""

import json
import zipfile
from pathlib import Path

from cvopt.context import UserContext
from cvopt.errors import InputError
from cvopt.ports import FileStore, RepositoryFactory

JSON_COLUMNS = {"keywords", "profiles", "payload", "response"}


class AccountService:
    def __init__(self, repos: RepositoryFactory, files: FileStore) -> None:
        self._repos = repos
        self._files = files

    async def export_user(self, user: UserContext, out_path: Path) -> Path:
        """One zip archive: records.json with every row, plus documents/ and runs/."""
        rows = await self._repos.for_user(user).export_rows()
        for table in rows.values():
            for row in table:
                for key in JSON_COLUMNS & row.keys():
                    row[key] = json.loads(row[key])
        user_dir = self._files.user_dir(user)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("records.json", json.dumps(rows, indent=2, ensure_ascii=False, default=str))
            for path in sorted(user_dir.rglob("*")):
                rel = path.relative_to(user_dir)
                if path.is_file() and rel.parts[0] in ("documents", "runs"):
                    z.write(path, rel.as_posix())
        out_path.chmod(0o600)
        return out_path

    async def delete_user(self, user: UserContext, confirmation: str) -> None:
        """Deletes every row and file of the user. `confirmation` is the email typed again."""
        if confirmation.strip().lower() != user.user_id:
            raise InputError("The confirmation does not match the user's email; nothing deleted.")
        repo = self._repos.for_user(user)
        async with repo.transaction():
            await repo.delete_user()
        self._files.delete_user_files(user)
