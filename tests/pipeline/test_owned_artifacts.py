"""Synthetic filesystem boundaries: no foreign artifact may enter an owned run."""

from pathlib import Path

import pytest

from cvopt.domain.run import JDSource, RunInfo
from cvopt.errors import InputError
from cvopt.services.publication import RunPublication
from tests.conftest import TEST_NOW
from tests.pipeline.test_publication import populate


def initial(files, user, name):
    run = files.new_run_dir(user, name)
    info = RunInfo(
        run_id=name,
        name=name,
        created_at=TEST_NOW,
        as_of=TEST_NOW.date(),
        jd_source=JDSource(type="text"),
        language="en",
        models={},
        prompt_versions={},
        config={},
    )
    (run / "run.json").write_text(info.model_dump_json())
    (run / "requirements.json").write_text(
        '{"title":"Owned role","company":"Example","language":"en",'
        '"requirements":[],"logistics":[]}'
    )
    return run


@pytest.mark.parametrize("artifact", ["run.json", "requirements.json"])
@pytest.mark.parametrize("publication_state", ["legacy", "published-alias", "published-pinned"])
@pytest.mark.parametrize("destination", ["other-user", "outside"])
async def test_generation_refuses_foreign_inputs_before_reading(
    container, alice, bob, tmp_path, monkeypatch, artifact, publication_state, destination
):
    run = initial(container.files, alice, "owned")
    foreign = initial(container.files, bob, "private")
    target = foreign / artifact
    if destination == "outside":
        target = tmp_path / "external.json"
        target.write_bytes((foreign / artifact).read_bytes())
    selected = None
    if publication_state != "legacy":
        info = (run / "run.json").read_bytes()
        with RunPublication(run).attempt() as stage:
            populate(stage, "prior")
            (stage.path / "run.json").write_bytes(info)
            stage.publish()
        selected = stage.path
    # Attack both direct inputs and the pinned metadata inside a selected generation.
    source = (
        selected / artifact
        if publication_state == "published-pinned" and artifact == "run.json"
        else run / artifact
    )
    source.unlink()
    source.symlink_to(target)
    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path.name not in ("run.json", "requirements.json", "external.json"), (
            "Source artifact was read before validating all inputs"
        )
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    with pytest.raises(InputError, match="artifact") as caught:
        await container.generation.generate(alice, "owned")
    assert str(target) not in str(caught.value)
    if selected:
        assert (run / "current-generation").resolve() == selected
        assert (run / "cv.json").read_text() == '{"marker": "prior"}'
    else:
        assert not (run / "current-generation").exists()


@pytest.mark.parametrize(
    "artifact",
    ["cv.json", "run.json", "requirements.json", "requirements-snapshot.json", "initial-run.json"],
)
@pytest.mark.parametrize("destination", ["other-user", "outside", "dangling", "cycle"])
async def test_legacy_adoption_validates_all_sources_before_any_copy(
    container, alice, bob, tmp_path, monkeypatch, artifact, destination
):
    import shutil

    run = initial(container.files, alice, "owned")
    foreign = initial(container.files, bob, "private")
    (run / "cv.json").write_text('{"marker":"prior"}')
    source = run / artifact
    source.unlink(missing_ok=True)
    target = foreign / "run.json"
    if destination in ("outside", "dangling"):
        target = tmp_path / "external.json"
        if destination == "outside":
            target.write_text("PRIVATE")
    if destination == "cycle":
        target = source
    source.symlink_to(target)
    pub = RunPublication(run)

    def forbidden_copy(*args, **kwargs):
        pytest.fail("Legacy copy began before all source artifacts were validated")

    monkeypatch.setattr(shutil, "copy2", forbidden_copy)
    with pytest.raises(InputError, match="artifact") as caught:
        pub._adopt()
    assert str(target) not in str(caught.value)
    assert not (run / "current-generation").exists()
    assert list(pub.generations.iterdir()) == []
    assert source.is_symlink()
    if artifact != "initial-run.json":
        assert not (run / "initial-run.json").exists()


@pytest.mark.parametrize("published", [False, True])
async def test_generation_accepts_owned_inputs_through_configured_root_alias(
    container, alice, tmp_path, monkeypatch, published
):
    from cvopt.adapters.files import LocalFileStore
    from cvopt.services import generation

    alias = tmp_path / "configured"
    alias.symlink_to(container.files.runs_dir(alice).parent.parent.parent, target_is_directory=True)
    files = LocalFileStore(alias)
    run = initial(files, alice, "owned")
    if published:
        info = (run / "run.json").read_bytes()
        with RunPublication(run).attempt() as stage:
            populate(stage)
            (stage.path / "run.json").write_bytes(info)
            stage.publish()
    monkeypatch.setattr(container.generation, "_files", files)

    class LoadedOwnedInput(Exception):
        pass

    def stop_after_loading(service, user, jd, as_of):
        assert jd.title == "Owned role"
        assert as_of == TEST_NOW.date()
        raise LoadedOwnedInput

    monkeypatch.setattr(generation, "_Pipeline", stop_after_loading)
    with pytest.raises(LoadedOwnedInput):
        await container.generation.generate(alice, "owned")


async def test_explicit_standalone_render_and_score_allow_external_file_aliases(
    container, alice, bob, tmp_path
):
    from pypdf import PdfReader

    foreign = initial(container.files, bob, "explicit-input")
    (foreign / "cv.json").write_text('{"basics":{"name":"Explicit Synthetic Person"}}')
    cv = tmp_path / "cv.json"
    requirements = tmp_path / "requirements.json"
    cv.symlink_to(foreign / "cv.json")
    requirements.symlink_to(foreign / "requirements.json")
    output = tmp_path / "explicit.pdf"
    rendered = container.render.render(cv, output)
    assert rendered.path == output
    assert "EXPLICIT SYNTHETIC PERSON" in PdfReader(output).pages[0].extract_text()
    scored = await container.scoring.score(alice, cv, requirements)
    assert scored.jd.title == "Owned role"
    assert scored.as_of == TEST_NOW.date()
