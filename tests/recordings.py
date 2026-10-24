"""Recorded LLM responses: tests/recordings/<task>.jsonl, one JSON object per call
(task, version, model, key, output). Loaded into the golden user's cache before a test;
in record mode (CVOPT_RECORD=1) live tests also start from them, and their new cache
rows are written back after the test. A prompt-version bump changes the keys, so those
calls are recorded afresh."""

import json
from pathlib import Path
from typing import Any

from cvopt.adapters.db.repository import UserRepository

RECORDINGS = Path(__file__).parent / "recordings"


def read_all() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(RECORDINGS.glob("*.jsonl")):
        rows += [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    return rows


async def load(repo: UserRepository) -> set[str]:
    rows = read_all()
    for r in rows:
        await repo.cache_put(r["key"], r["task"], r["version"], r["model"], r["output"])
    return {r["key"] for r in rows}


async def write_new(repo: UserRepository, known: set[str]) -> int:
    new = [r for r in await repo.cache_rows() if r["key"] not in known]
    for r in new:
        line = {
            "task": r["task"],
            "version": r["prompt_version"],
            "model": r["model"],
            "key": r["key"],
            "output": r["response"],
        }
        with (RECORDINGS / f"{r['task']}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    return len(new)
