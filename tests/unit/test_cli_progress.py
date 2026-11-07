"""Progress is terminal-only stderr; JSON stdout is always one complete value."""

import json
from io import StringIO

from rich.console import Console
from typer.testing import CliRunner

from cvopt.cli.main import app


def test_progress_is_safe_and_silent_off_terminal():
    from cvopt.cli.progress import TerminalProgress
    from cvopt.observability import ProgressEvent

    stream = StringIO()
    event = ProgressEvent(
        "op",
        "generation",
        "stage",
        "succeeded",
        stage="matching",
        duration_s=0.25,
        llm_calls=2,
        application_cache_hits=3,
    )
    TerminalProgress(Console(file=stream, force_terminal=False))(event)
    assert stream.getvalue() == ""
    TerminalProgress(Console(file=stream, force_terminal=True))(event)
    assert "matching" in stream.getvalue()
    assert "2 calls" in stream.getvalue() and "3 cache" in stream.getvalue()


def test_score_json_with_report_notice_has_clean_stdout(settings, tmp_path):
    from cvopt.domain.jd import JobDescription, Requirement

    cv = tmp_path / "cv.json"
    cv.write_text('{"languages":[{"language":"English","fluency":"C2"}]}')
    jd = JobDescription(
        title="Example",
        company="Example",
        language="en",
        logistics=[],
        requirements=[
            Requirement(
                id="R1",
                text="English C1",
                category="language",
                importance="must",
                keywords=[],
                synonyms=[],
                language_code="en",
                language_level="C1",
            )
        ],
    )
    req = tmp_path / "requirements.json"
    req.write_text(jd.model_dump_json())
    result = CliRunner().invoke(
        app,
        [
            "--user",
            "a@example.com",
            "--data-root",
            str(settings.app.data_root),
            "score",
            str(cv),
            "--requirements",
            str(req),
            "--json",
            "--report",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["total"] == 100
    assert "Wrote" not in result.stdout
    assert "Wrote" in result.stderr
