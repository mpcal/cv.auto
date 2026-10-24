import json
from pathlib import Path

from typer.testing import CliRunner

from cvopt import __version__
from cvopt.cli.main import app

runner = CliRunner()


def test_version(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["--user", "a@example.com", "--data-root", str(tmp_path), "version"]
    )
    assert result.exit_code == 0, result.output
    assert __version__ in result.output
    log_file = tmp_path / "cvopt.log"
    assert log_file.stat().st_mode & 0o077 == 0


def test_debug_writes_json_log(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--data-root", str(tmp_path), "--debug", "version"])
    assert result.exit_code == 0, result.output
    lines = (tmp_path / "cvopt.log").read_text().splitlines()
    assert json.loads(lines[-1])["command"] == "version"


def test_invalid_user_exits_with_input_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--user", "nope", "--data-root", str(tmp_path), "version"])
    assert result.exit_code == 1


def test_bad_config_exits_with_input_error(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[app]\nbogus = 1\n")
    result = runner.invoke(app, ["--config", str(cfg), "version"])
    assert result.exit_code == 1
