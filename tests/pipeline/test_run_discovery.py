"""Run discovery reads only owned local artifacts, including older publications."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvopt.cli.main import app
from cvopt.errors import InputError, LLMTaskError
from tests.pipeline.test_publication import populate


def initial(container, user, name):
    run = container.files.new_run_dir(user, name)
    (run / "run.json").write_text(json.dumps({"created_at": "2026-01-01T00:00:00Z"}))
    (run / "requirements.json").write_text(json.dumps({"title": "Synthetic", "company": "Example"}))
    return run


async def test_list_show_new_legacy_failed_and_committed_status(container, alice, bob):
    from cvopt.services.publication import RunPublication

    new = initial(container, alice, "new")
    legacy = initial(container, alice, "legacy")
    (legacy / "cv.json").write_text("{}")
    (legacy / "report.json").write_text('{"total":42}')
    current = initial(container, alice, "current")
    pub = RunPublication(current)
    with pub.attempt() as stage:
        populate(stage)
        (stage.path / "run.json").write_text(json.dumps({"generated_at": "2026-02-01T00:00:00Z"}))
        (stage.path / "report.json").write_text('{"total":83,"profile_supported_score":61}')
        stage.publish()
    with pytest.raises(LLMTaskError), pub.attempt():
        raise LLMTaskError("PRIVATE")
    failed = container.runs.show(alice, "current")
    assert failed.published
    assert failed.status == "published"
    assert failed.last_attempt["status"] == "failed"
    assert failed.target_score == 83
    assert failed.profile_supported_score == 61
    assert failed.published_at == "2026-02-01T00:00:00Z"
    assert set(failed.artifacts) >= {"cv.pdf", "cv-annotated.pdf"}
    assert all(Path(p).is_relative_to(current) for p in failed.artifacts.values())
    assert "PRIVATE" not in failed.model_dump_json()
    with pytest.raises(InputError):
        container.runs.show(bob, str(current))
    (new / "escape").symlink_to(current)
    assert container.runs.list(bob) == []
    assert {r.name for r in container.runs.list(alice)} == {"current", "legacy", "new"}
    old = container.runs.show(alice, "leg")
    assert old.published and old.publication_status == "legacy"
    assert old.target_score == 42 and old.profile_supported_score is None
    assert old.usage is None


async def test_pointer_is_authoritative_with_incomplete_attempt_diagnostics(container, alice):
    from cvopt.services.publication import RunPublication

    run = initial(container, alice, "committed")
    with RunPublication(run).attempt() as stage:
        populate(stage)
        stage.publish()
    attempt = json.loads((run / "last-attempt.json").read_text())
    attempt.update(status="running", published=False, finished_at=None)
    (run / "last-attempt.json").write_text(json.dumps(attempt))
    shown = container.runs.show(alice, "committed")
    assert shown.published
    assert shown.attempt_diagnostics == "incomplete"
    assert shown.selected_generation_id == attempt["generation_id"]


async def test_discovery_does_not_follow_cross_user_artifacts(container, alice, bob):
    owned = initial(container, alice, "owned")
    private = initial(container, bob, "private")
    (private / "report.json").write_text('{"total":99}')
    (owned / "report.json").symlink_to(private / "report.json")
    (owned / "cv.pdf").symlink_to(private / "report.json")
    shown = container.runs.show(alice, "owned")
    assert shown.target_score is None
    assert "cv.pdf" not in shown.artifacts


@pytest.mark.parametrize("command", ["list", "show"])
def test_discovery_json_stdout_remains_clean_with_export(settings, tmp_path, command):
    target = tmp_path / "runs.json"
    args = ["--user", "alice@example.com", "--data-root", str(settings.app.data_root)]
    runner = CliRunner()
    if command == "show":
        # Construct a minimal legacy run without any provider calls or API key.
        from cvopt.adapters.files import LocalFileStore
        from cvopt.context import UserContext

        run = LocalFileStore(settings.app.data_root).new_run_dir(
            UserContext.from_email("alice@example.com"), "new"
        )
        (run / "run.json").write_text("{}")
    result = runner.invoke(
        app,
        [
            *args,
            "run",
            command,
            *(["new"] if command == "show" else []),
            "--json",
            "--out",
            str(target),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == json.loads(target.read_text())
    assert "Wrote" not in result.stdout
    assert "Wrote" in result.stderr


async def test_discovery_refuses_a_runs_root_redirect_to_another_user(container, alice, bob):
    private = initial(container, bob, "private")
    (container.files.user_dir(alice) / "runs").symlink_to(private.parent)
    with pytest.raises(InputError):
        container.runs.list(alice)
    with pytest.raises(InputError):
        container.runs.show(alice, "private")


async def test_discovery_distinguishes_failed_first_attempt_and_postcommit_error(container, alice):
    from cvopt.services.publication import RunPublication

    first = initial(container, alice, "first")
    with pytest.raises(LLMTaskError), RunPublication(first).attempt():
        raise LLMTaskError("PRIVATE")
    shown = container.runs.show(alice, "first")
    assert not shown.published and shown.status == "failed"
    assert shown.published_at is None
    assert shown.last_attempt_at is not None
    with pytest.raises(LLMTaskError), RunPublication(first).attempt() as stage:
        populate(stage)
        stage.publish()
        raise LLMTaskError("PRIVATE")
    shown = container.runs.show(alice, "first")
    assert shown.published
    assert shown.last_attempt["status"] == "published_with_error"
    assert shown.last_attempt["published"]
    assert shown.attempt_diagnostics == "available"


@pytest.mark.parametrize("command", ["list", "show"])
async def test_cyclic_generation_pointer_cannot_break_other_run_discovery(
    container, alice, command
):
    initial(container, alice, "healthy")
    broken = initial(container, alice, "cyclic")
    (broken / "current-generation").symlink_to("current-generation")
    if command == "show":
        with pytest.raises(InputError):
            container.runs.show(alice, "cyclic")
    else:
        assert [run.name for run in container.runs.list(alice)] == ["healthy"]


@pytest.mark.parametrize("boundary", ["current-generation", ".generations"])
async def test_selection_resolution_oserror_is_a_controlled_input_error(
    container, alice, monkeypatch, boundary
):
    broken = initial(container, alice, "unreadable")
    generations = broken / ".generations"
    generations.mkdir()
    selected = generations / "generation"
    selected.mkdir()
    (broken / "current-generation").symlink_to(selected.relative_to(broken))
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == broken / boundary:
            raise OSError("PRIVATE filesystem details")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(InputError) as caught:
        container.runs.show(alice, "unreadable")
    assert "PRIVATE" not in str(caught.value)
    assert container.runs.list(alice) == []
