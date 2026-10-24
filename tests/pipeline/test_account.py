"""Account (F8): export contains all of a user's records and files; deleting a user
leaves nothing of theirs and other users' data unchanged."""

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from cvopt.adapters.db import schema as s
from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import ClaimType
from cvopt.errors import InputError
from tests.scenarios import JD, ingest_golden


async def seed_bob(c: Container, bob: UserContext) -> None:
    pos = await c.data.create_position(
        bob, {"employer": "Bobco", "title": "Engineer", "start_month": "2019-01", "end_month": None}
    )
    await c.data.create_claim(bob, type=ClaimType.SKILL, text="Go", text_lang="en", position=pos.id)
    await c.data.update_profile(bob, {"name": "Bob"})
    await c.repos.for_user(bob).cache_put("bobkey", "task", "1", "model", "{}")
    run = c.files.new_run_dir(bob, "2026-01-01T0000-bobco-engineer")
    (run / "requirements.json").write_text("{}")


async def count_rows(c: Container, user: UserContext) -> dict[str, int]:
    async with c.engine.connect() as conn:
        return {
            t.name: int(
                await conn.scalar(
                    select(func.count()).select_from(t).where(t.c.user_id == user.user_id)
                )
                or 0
            )
            for t in s.metadata.sorted_tables
        }


async def snapshot(c: Container, user: UserContext, tmp: Path) -> tuple[object, list[str]]:
    path = await c.account.export_user(user, tmp / f"{user.user_key}.zip")
    with zipfile.ZipFile(path) as z:
        records = json.loads(z.read("records.json"))
        for table in records.values():  # timestamps are part of the data, keep them
            table.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        return records, sorted(z.namelist())


async def test_export_contains_all_records_and_files(
    golden: Container, alice: UserContext, bob: UserContext, tmp_path: Path
) -> None:
    await ingest_golden(golden, alice)
    run = await golden.jd.create_run(alice, text=JD / "jd_en_nordwind.txt")
    await seed_bob(golden, bob)

    records, names = await snapshot(golden, alice, tmp_path)
    counts = await count_rows(golden, alice)
    assert {t: len(rows) for t, rows in records.items()} == counts  # type: ignore[union-attr]
    for table in (
        "users",
        "profile",
        "position",
        "education",
        "language",
        "certificate",
        "claim",
        "evidence",
        "source_document",
        "fact_source",
        "review_batch",
        "proposal_item",
    ):
        assert counts[table] > 0, table
    assert all(r["user_id"] == alice.user_id for rows in records.values() for r in rows)  # type: ignore[union-attr]
    for doc in await golden.data.list_documents(alice):
        assert f"documents/{doc.id}{Path(doc.filename).suffix}" in names
    assert f"runs/{run.name}/requirements.json" in names
    assert not any("bob" in n for n in names)
    claims = records["claim"]  # type: ignore[index]
    assert isinstance(claims[0]["keywords"], list)  # JSON columns are decoded


async def test_delete_user_leaves_nothing_and_spares_others(
    golden: Container, alice: UserContext, bob: UserContext, tmp_path: Path
) -> None:
    await ingest_golden(golden, alice)
    await golden.jd.create_run(alice, text=JD / "jd_en_nordwind.txt")
    await seed_bob(golden, bob)
    bob_before = await snapshot(golden, bob, tmp_path)
    alice_dir = golden.files.user_dir(alice)

    with pytest.raises(InputError):
        await golden.account.delete_user(alice, "bob@example.com")
    assert (await count_rows(golden, alice))["claim"] > 0

    await golden.account.delete_user(alice, " Alice@Example.com ")
    assert set((await count_rows(golden, alice)).values()) == {0}
    assert not alice_dir.exists()
    assert await snapshot(golden, bob, tmp_path) == bob_before
    assert await golden.data.list_claims(alice) == []
