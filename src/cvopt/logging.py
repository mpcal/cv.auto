"""JSON-lines log formatter and setup."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line; `extra=` fields are included as top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def setup_logging(log_file: Path, level: str) -> None:
    """Log to `log_file`, creating its directory readable by the owner only."""
    log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_file.touch(mode=0o600, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(JsonLinesFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
