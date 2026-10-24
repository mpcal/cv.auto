from datetime import date, datetime
from typing import Any, Literal

from cvopt.domain.base import Model


class JDSource(Model):
    type: Literal["url", "pdf", "text"]
    url: str = ""
    final_url: str = ""
    filename: str = ""


class RunInfo(Model):
    """run.json: what a run was made from, so it can be rescored and reproduced."""

    run_id: str
    name: str  # the folder name
    created_at: datetime
    as_of: date  # reused when the CV is rescored
    jd_source: JDSource
    language: str
    models: dict[str, str]
    prompt_versions: dict[str, str]
    config: dict[str, Any]
