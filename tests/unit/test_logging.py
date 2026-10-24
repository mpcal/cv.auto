import json
import logging

from cvopt.logging import JsonLinesFormatter


def test_json_line_with_extra_fields() -> None:
    record = logging.LogRecord("cvopt.x", logging.INFO, "f", 1, "done %s", ("now",), None)
    record.task = "parse_jd"
    entry = json.loads(JsonLinesFormatter().format(record))
    assert entry["msg"] == "done now"
    assert entry["level"] == "INFO"
    assert entry["task"] == "parse_jd"
