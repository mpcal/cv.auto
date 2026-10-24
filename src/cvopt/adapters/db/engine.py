"""Engine factory: SQLite pragmas on every connection, migrations before the first query."""

import asyncio
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

MIGRATIONS = Path(__file__).parent / "migrations"


def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def alembic_config(sync_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def migrate(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database:
        Path(url.database).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    sync = url.set(drivername=url.get_backend_name())
    command.upgrade(alembic_config(sync.render_as_string(hide_password=False)), "head")


async def open_database(database_url: str) -> AsyncEngine:
    await asyncio.to_thread(migrate, database_url)
    engine = create_async_engine(database_url)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _set_pragmas)
    return engine
