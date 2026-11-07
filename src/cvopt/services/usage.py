"""Publication envelopes keep one intake contribution separate from new billing."""

import json
from pathlib import Path


def publication_usage(run: Path, operation: dict[str, object]) -> dict[str, object]:
    intake: dict[str, object] | None = None
    path = run / "intake-usage.json"
    try:
        if path.resolve().is_relative_to(run.resolve()):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema_version") == 2:
                intake = data
    except (OSError, ValueError):
        pass
    return {
        "schema_version": 2,
        "operation": operation,
        "jd_intake": intake,
        "jd_intake_billed_in_operation": False,
        "historical_usage": "unavailable",
    }
