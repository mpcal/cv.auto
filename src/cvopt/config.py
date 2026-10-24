"""Settings models; loading from TOML and the environment."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from cvopt.errors import InputError

DEFAULT_CONFIG_PATH = Path("~/.config/cvopt/config.toml")


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppSettings(Section):
    data_root: Path = Path("~/.local/share/cvopt")
    database_url: str = "sqlite+aiosqlite:///{data_root}/cvopt.sqlite"
    default_user: str = ""
    log_level: str = "INFO"
    debug_text_logging: bool = False


class LLMSettings(Section):
    provider: str = "anthropic"
    strong_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    max_concurrency: int = 4
    request_timeout_s: float = 120
    max_retries: int = 5
    schema_retries: int = 2

    def model_for(self, tier: str) -> str:
        return self.strong_model if tier == "strong" else self.fast_model


class ScoringSettings(Section):
    weight_must: float = 3
    weight_nice: float = 1
    match_exact: float = 1.0
    match_synonym: float = 0.9
    match_semantic: float = 0.6
    match_partial: float = 0.3
    placement_recent_highlight: float = 1.0
    placement_older_highlight: float = 0.85
    placement_headline_summary: float = 0.8
    placement_skills: float = 0.7
    recency_years: int = 5


class OptimizerSettings(Section):
    exaggeration_min_gain_pp: float = 1.0
    # No invented statement for a requirement a real claim already matches at this level.
    invent_below_match: Literal["exact", "synonym", "semantic", "partial"] = "semantic"
    tie_window_pp: float = 0.1
    page_budget: int = 2
    summary_min_sentences: int = 2
    summary_max_sentences: int = 4
    max_render_iterations: int = 20


class LayoutSettings(Section):
    # Calibrated with scripts/calibrate_layout.py (Source Sans 3, 10 pt, 18 mm margins).
    chars_per_line: int = 112
    lines_per_page: int = 62


class DocumentSettings(Section):
    max_mb: float = 20
    max_pdf_pages: int = 50
    min_text_chars: int = 200


class ReportSettings(Section):
    timeline_gap_months: int = 3


class MatchingSettings(Section):
    employer_similarity: int = 85
    judge_batch_size: int = 40


class FetchSettings(Section):
    timeout_s: float = 20
    max_bytes: int = 5_000_000
    min_main_text_chars: int = 500


class RenderSettings(Section):
    font: str = "Source Sans 3"
    font_size_pt: float = 10
    margin_mm: float = 18


class Settings(Section):
    app: AppSettings = AppSettings()
    llm: LLMSettings = LLMSettings()
    scoring: ScoringSettings = ScoringSettings()
    optimizer: OptimizerSettings = OptimizerSettings()
    layout: LayoutSettings = LayoutSettings()
    documents: DocumentSettings = DocumentSettings()
    report: ReportSettings = ReportSettings()
    matching: MatchingSettings = MatchingSettings()
    fetch: FetchSettings = FetchSettings()
    render: RenderSettings = RenderSettings()


def load_settings(config: Path | None = None, data_root: Path | None = None) -> Settings:
    """Read the config file (--config, else CVOPT_CONFIG, else the default path).

    A missing default file means all defaults; a missing explicit file is an error.
    """
    explicit = config or (Path(p) if (p := os.environ.get("CVOPT_CONFIG")) else None)
    path = (explicit or DEFAULT_CONFIG_PATH).expanduser()
    raw: dict[str, object] = {}
    if path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as err:
            raise InputError(f"Cannot read config file {path}: {err}") from err
    elif explicit:
        raise InputError(f"Config file not found: {path}")
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as err:
        raise InputError(f"Invalid config file {path}:\n{err}") from err

    root = (data_root or settings.app.data_root).expanduser()
    app = settings.app.model_copy(
        update={
            "data_root": root,
            "database_url": settings.app.database_url.replace("{data_root}", str(root)),
        }
    )
    return settings.model_copy(update={"app": app})
