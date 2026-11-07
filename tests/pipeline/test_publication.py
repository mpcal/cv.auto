"""Atomic publication tests use synthetic documents and real local files."""

import json
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from cvopt.errors import LLMTaskError, ValidationFailed


def populate(stage, marker: str = "new") -> None:
    for name in (
        "cv.json",
        "annotations.json",
        "report.json",
        "candidates.json",
        "run.json",
        "requirements.json",
        "profile-snapshot.json",
        "usage.json",
    ):
        (stage.path / name).write_text(json.dumps({"marker": marker}))
    (stage.path / "report.csv").write_text(marker)
    for name in ("cv.pdf", "cv-annotated.pdf"):
        pdf = PdfWriter()
        pdf.add_blank_page(width=100, height=100)
        pdf.write(stage.path / name)


def publication(run: Path):
    from cvopt.services.publication import RunPublication

    return RunPublication(run)


def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "requirements.json").write_text('{"marker":"editable"}')
    (run / "run.json").write_text('{"marker":"initial"}')
    return run


def test_publication_stages_complete_outputs_and_preserves_editable_input(tmp_path):
    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as stage:
        populate(stage)
        assert not (run / "cv.json").exists()
        stage.publish()
    assert (run / "current-generation").is_symlink()
    assert (run / "cv.json").is_symlink()
    assert json.loads((run / "cv.json").read_text()) == {"marker": "new"}
    assert json.loads((run / "requirements.json").read_text()) == {"marker": "editable"}
    assert json.loads((run / "requirements-snapshot.json").read_text()) == {"marker": "new"}
    assert json.loads((run / "initial-run.json").read_text()) == {"marker": "initial"}
    assert json.loads((run / "last-attempt.json").read_text())["status"] == "succeeded"


@pytest.mark.parametrize("fault", ["render", "invalid_pdf", "invalid_annotated_pdf", "pointer"])
def test_failure_preserves_old_generation_and_records_safe_attempt(tmp_path, monkeypatch, fault):
    import os

    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as old:
        populate(old, "old")
        old.publish()
    before = (run / "current-generation").resolve()
    real_replace = os.replace

    def replace(source, destination):
        if Path(destination) == run / "current-generation":
            raise OSError("SECRET-CV provider details")
        real_replace(source, destination)

    with pytest.raises((LLMTaskError, ValidationFailed, OSError)), pub.attempt() as stage:
        populate(stage)
        if fault == "render":
            raise LLMTaskError("SECRET-CV provider details")
        if fault in ("invalid_pdf", "invalid_annotated_pdf"):
            name = "cv.pdf" if fault == "invalid_pdf" else "cv-annotated.pdf"
            (stage.path / name).write_bytes(b"broken")
        if fault == "pointer":
            monkeypatch.setattr(os, "replace", replace)
        stage.publish()
    assert (run / "current-generation").resolve() == before
    assert json.loads((run / "cv.json").read_text()) == {"marker": "old"}
    assert "SECRET-CV" not in (run / "last-attempt.json").read_text()
    assert json.loads((run / "last-attempt.json").read_text())["status"] == "failed"


def test_legacy_adoption_preserves_all_outputs_on_failed_pointer_switch(tmp_path, monkeypatch):
    import os

    run = run_dir(tmp_path)
    (run / "cv.json").write_text('{"marker":"legacy"}')
    (run / "report.json").write_text('{"marker":"legacy-report"}')
    pub = publication(run)
    replace = os.replace

    def fail_new_pointer(source, destination):
        if (
            Path(destination) == run / "current-generation"
            and (run / "current-generation").exists()
        ):
            raise OSError("switch failed")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_new_pointer)
    with pytest.raises(OSError), pub.attempt() as stage:
        populate(stage)
        stage.publish()
    assert json.loads((run / "cv.json").read_text()) == {"marker": "legacy"}
    assert json.loads((run / "report.json").read_text()) == {"marker": "legacy-report"}
    assert (run / "cv.json").is_symlink()
    assert json.loads((run / "run.json").read_text()) == {"marker": "initial"}


def test_pinned_inputs_and_output_aliases_stay_with_one_generation(tmp_path):
    from cvopt.services.publication import pin_paths

    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as old:
        populate(old, "old")
        old.publish()
    cv, notes, output, requirements = pin_paths(
        run / "cv.json", run / "annotations.json", run / "cv.pdf", run / "requirements.json"
    )
    with pub.attempt() as new:
        populate(new, "new")
        new.publish()
    assert cv.parent == notes.parent == output.parent == requirements.parent == old.path
    assert json.loads(cv.read_text()) == {"marker": "old"}
    assert json.loads(requirements.read_text()) == {"marker": "old"}
    output.write_bytes(b"old standalone output")
    assert (run / "cv.pdf").read_bytes() != b"old standalone output"


async def test_generation_publishes_actual_snapshots_and_both_pdfs(golden, alice, monkeypatch):
    from tests.scenarios import generate_golden

    results = []
    generate = golden.generation.generate

    async def capture(*args):
        result = await generate(*args)
        results.append(result)
        return result

    monkeypatch.setattr(golden.generation, "generate", capture)
    runs = await generate_golden(golden, alice)
    for run in runs.values():
        assert (run / "cv.pdf").is_file()
        assert (run / "cv-annotated.pdf").is_file()
        assert (run / "requirements.json").resolve() != (
            run / "requirements-snapshot.json"
        ).resolve()
        snapshot = json.loads((run / "profile-snapshot.json").read_text())
        assert snapshot["profile"]["name"] == "Alice Example"
        assert snapshot["claims"]
        info = json.loads((run / "run.json").read_text())
        assert info["generated_at"]
        assert "judge_match" in info["prompt_versions"]
        assert "judge_match_pair" in info["prompt_versions"]
    for result in results:
        assert result.run_dir / "requirements-snapshot.json" in result.files
        assert result.run_dir / "requirements.json" not in result.files


def test_render_pins_metadata_and_default_output_during_pointer_switch(tmp_path, monkeypatch):
    from cvopt.adapters.typst_renderer import TypstRenderer
    from cvopt.config import Settings
    from cvopt.services import render as module
    from cvopt.services.render import RenderService

    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as old:
        populate(old, "old")
        (old.path / "cv.json").write_text('{"basics":{"name":"Old Person"}}')
        (old.path / "run.json").write_text('{"language":"de"}')
        old.publish()
    with pub.attempt() as new:
        populate(new, "new")
        (new.path / "cv.json").write_text('{"basics":{"name":"New Person"}}')
        (new.path / "run.json").write_text('{"language":"en"}')
        load = module.load_cv

        def switch_after_read(path):
            cv = load(path)
            new.publish()
            return cv

        monkeypatch.setattr(module, "load_cv", switch_after_read)
        result = RenderService(TypstRenderer(), Settings()).render(run / "cv.json", run / "cv.pdf")
        assert result.path == old.path / "cv.pdf"
    text = PdfReader(old.path / "cv.pdf").pages[0].extract_text()
    assert "OLD PERSON" in text and "NEW PERSON" not in text
    assert (run / "cv.pdf").resolve() == new.path / "cv.pdf"


async def test_scoring_pins_requirement_and_date_during_pointer_switch(
    container, alice, tmp_path, monkeypatch
):
    from cvopt.domain.run import JDSource, RunInfo
    from cvopt.services import scoring as module
    from tests.conftest import TEST_NOW

    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as old:
        populate(old, "old")
        (old.path / "cv.json").write_text('{"basics":{"name":"Old Person"}}')
        (old.path / "requirements.json").write_text(
            '{"title":"Old job","company":"Old company","language":"en",'
            '"requirements":[],"logistics":[]}'
        )
        info = RunInfo(
            run_id="synthetic",
            name="run",
            created_at=TEST_NOW,
            as_of=TEST_NOW.date(),
            jd_source=JDSource(type="text"),
            language="en",
            models={},
            prompt_versions={},
            config={},
        )
        (old.path / "run.json").write_text(info.model_dump_json())
        old.publish()
    with pub.attempt() as new:
        populate(new, "new")
        load = module.load_cv

        def switch_after_read(path):
            cv = load(path)
            new.publish()
            return cv

        monkeypatch.setattr(module, "load_cv", switch_after_read)
        scored = await container.scoring.score(alice, run / "cv.json", run / "requirements.json")
    assert scored.jd.title == "Old job"
    assert scored.as_of == TEST_NOW.date()


def test_explicit_standalone_cv_beside_publication_keeps_its_own_path(tmp_path):
    from cvopt.adapters.typst_renderer import TypstRenderer
    from cvopt.config import Settings
    from cvopt.services.render import RenderService

    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as stage:
        populate(stage)
        stage.publish()
    custom = run / "hand-edited.json"
    custom.write_text('{"basics":{"name":"Explicit Person"}}')
    result = RenderService(TypstRenderer(), Settings()).render(custom, run / "explicit.pdf")
    assert result.path == run / "explicit.pdf"
    assert "EXPLICIT PERSON" in PdfReader(result.path).pages[0].extract_text()


async def test_failed_first_generation_keeps_initial_metadata_and_no_publication(golden, alice):
    from tests.scenarios import JD

    run = await golden.jd.create_run(alice, text=JD / "jd_en_nordwind.txt")
    initial = (run.path / "run.json").read_bytes()
    with pytest.raises(Exception, match="No confirmed claims"):
        await golden.generation.generate(alice, run.name)
    assert (run.path / "run.json").read_bytes() == initial
    assert not (run.path / "cv.json").exists()
    assert not (run.path / "current-generation").exists()
    attempt = json.loads((run.path / "last-attempt.json").read_text())
    assert attempt["status"] == "failed" and attempt["error_type"] == "input"


@pytest.mark.parametrize("status_failure", [False, True])
def test_exception_after_publication_retains_committed_artifacts(
    tmp_path, monkeypatch, status_failure
):
    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as old:
        populate(old, "old")
        old.publish()

    def fail_status(status):
        raise OSError("SECRET status write failed")

    with pytest.raises(ValueError, match="post-publication failure"), pub.attempt() as new:
        populate(new, "committed")
        new.publish()
        if status_failure:
            monkeypatch.setattr(pub, "_status", fail_status)
        raise ValueError("post-publication failure SECRET")
    assert new.published
    assert old.path.is_dir() and new.path.is_dir()
    assert (run / "current-generation").resolve() == new.path
    assert json.loads((run / "cv.json").read_text()) == {"marker": "committed"}
    if not status_failure:
        attempt = json.loads((run / "last-attempt.json").read_text())
        assert attempt["status"] == "published_with_error"
        assert attempt["published"] is True
        assert attempt["error_type"] == "internal"
    assert "SECRET" not in (run / "last-attempt.json").read_text()


@pytest.mark.parametrize("persistent", [False, True])
def test_final_status_write_failure_retains_committed_artifacts(tmp_path, monkeypatch, persistent):
    run = run_dir(tmp_path)
    pub = publication(run)
    write_status = pub._status

    def fail_final_status(status):
        if status["status"] == "succeeded" or (persistent and status["status"] != "running"):
            raise OSError("SECRET status write failed")
        write_status(status)

    monkeypatch.setattr(pub, "_status", fail_final_status)
    with pytest.raises(OSError, match="status write failed"), pub.attempt() as stage:
        populate(stage, "committed")
        stage.publish()
    assert stage.published and stage.path.is_dir()
    assert (run / "current-generation").resolve() == stage.path
    assert json.loads((run / "cv.json").read_text()) == {"marker": "committed"}
    attempt = json.loads((run / "last-attempt.json").read_text())
    assert attempt["generation_id"] == stage.path.name
    assert attempt["status"] == ("running" if persistent else "published_with_error")
    if not persistent:
        assert attempt["published"] is True
    assert "SECRET" not in (run / "last-attempt.json").read_text()


@pytest.mark.parametrize("older", [False, True])
def test_symlink_render_uses_actual_standalone_or_older_generation_target(tmp_path, older):
    from cvopt.adapters.typst_renderer import TypstRenderer
    from cvopt.config import Settings
    from cvopt.services.publication import pin_paths
    from cvopt.services.render import RenderService

    run = run_dir(tmp_path)
    pub = publication(run)
    with pub.attempt() as old:
        populate(old, "old")
        (old.path / "cv.json").write_text('{"basics":{"name":"Explicit Person"}}')
        (old.path / "run.json").write_text('{"language":"de"}')
        old.publish()
    with pub.attempt() as current:
        populate(current, "current")
        current.publish()
    standalone = run / "source.json"
    standalone.write_text('{"basics":{"name":"Explicit Person"}}')
    link = run / "hand-edited.json"
    target = old.path / "cv.json" if older else standalone
    link.symlink_to(target)
    output = run / "explicit.pdf"
    cv, pinned_output = pin_paths(link, output)
    assert cv == target
    assert pinned_output == (old.path / "explicit.pdf" if older else output)
    result = RenderService(TypstRenderer(), Settings()).render(link, output)
    assert result.path == pinned_output
    assert "EXPLICIT PERSON" in PdfReader(result.path).pages[0].extract_text()
    assert json.loads((run / "cv.json").read_text()) == {"marker": "current"}


def test_pointer_selection_commits_even_if_publish_return_is_interrupted(tmp_path, monkeypatch):
    run = run_dir(tmp_path)
    pub = publication(run)
    publish = pub._publish

    def fail_after_selection(path):
        publish(path)
        raise OSError("failure after pointer selection")

    monkeypatch.setattr(pub, "_publish", fail_after_selection)
    with pytest.raises(OSError, match="after pointer selection"), pub.attempt() as stage:
        populate(stage, "committed")
        stage.publish()
    assert stage.published and stage.path.is_dir()
    assert (run / "current-generation").resolve() == stage.path
    assert json.loads((run / "cv.json").read_text()) == {"marker": "committed"}
    attempt = json.loads((run / "last-attempt.json").read_text())
    assert attempt["status"] == "published_with_error" and attempt["published"] is True
