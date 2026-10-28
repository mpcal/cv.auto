from pathlib import Path

import pytest

from cvopt.config import load_settings
from cvopt.errors import InputError


def test_defaults_without_config_file() -> None:
    s = load_settings()
    assert s.llm.strong_model == "claude-sonnet-5-5"
    assert s.app.data_root == Path("~/.local/share/cvopt").expanduser()
    assert s.app.database_url == f"sqlite+aiosqlite:///{s.app.data_root}/cvopt.sqlite"


def test_file_values_and_data_root_override(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('[optimizer]\npage_budget = 1\n[app]\ndata_root = "/x"\n')
    assert load_settings(cfg).app.data_root == Path("/x")
    s = load_settings(cfg, tmp_path / "d")
    assert s.optimizer.page_budget == 1
    assert s.app.data_root == tmp_path / "d"


def test_env_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[layout]\nlines_per_page = 50\n")
    monkeypatch.setenv("CVOPT_CONFIG", str(cfg))
    assert load_settings().layout.lines_per_page == 50


@pytest.mark.parametrize("text", ["[app]\nbogus = 1\n", "[nope]\n", "not toml ="])
def test_bad_config_raises_input_error(tmp_path: Path, text: str) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text(text)
    with pytest.raises(InputError):
        load_settings(cfg)


def test_missing_explicit_config_raises(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        load_settings(tmp_path / "missing.toml")


def test_layout_defaults_are_calibrated() -> None:
    # Values printed by scripts/calibrate_layout.py; re-run it after changing the template.
    s = load_settings()
    assert (s.layout.chars_per_line, s.layout.lines_per_page) == (112, 57)


def test_render_template_configuration(tmp_path: Path) -> None:
    assert load_settings().render.template == "atsthetic"
    cfg = tmp_path / "render.toml"
    cfg.write_text('[render]\ntemplate = "classic"\n')
    assert load_settings(cfg).render.template == "classic"
    cfg.write_text('[render]\ntemplate = "unknown"\n')
    with pytest.raises(InputError):
        load_settings(cfg)
