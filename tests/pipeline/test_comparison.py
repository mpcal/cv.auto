"""Offline role comparison uses current support, never regenerated target scores."""

import json
from datetime import date

import pytest
from typer.testing import CliRunner

from cvopt.cli.main import app
from cvopt.domain.enums import ClaimType, Importance, MatchLevel
from cvopt.errors import InputError
from cvopt.llm.judge import Judge
from cvopt.services.profile import ProfileCoverageService
from tests.pipeline.test_profile_coverage import job


async def semantic(self, requirement, texts):
    return {
        text: MatchLevel.SEMANTIC
        if requirement.text.casefold().strip() in text.casefold()
        else MatchLevel.NONE
        for text in texts
    }


def create_run(c, user, name, jd=None, run_id=None):
    run = c.files.new_run_dir(user, name)
    (run / "requirements.json").write_text((jd or job()).model_dump_json())
    (run / "run.json").write_text(json.dumps({"run_id": run_id or name}))
    return run


async def test_common_snapshot_date_config_rank_and_no_implicit_exports(
    container, alice, monkeypatch
):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    await container.data.create_claim(alice, type=ClaimType.SKILL, text="Python", text_lang="en")
    create_run(container, alice, "same-prefix-b", run_id="B")
    create_run(container, alice, "same-prefix-a", run_id="A")
    missing = job().model_copy(deep=True)
    missing.requirements[0] = missing.requirements[0].model_copy(
        update={"text": "Rust", "keywords": ["Rust"]}
    )
    create_run(container, alice, "missing", missing)
    seen = []
    original = ProfileCoverageService.coverage

    async def coverage(self, user, jd, as_of, **kw):
        seen.append((id(kw["facts"]), as_of))
        return await original(self, user, jd, as_of, **kw)

    monkeypatch.setattr(ProfileCoverageService, "coverage", coverage)
    svc = getattr(container, "comparison", None)
    assert svc is not None, "Comparison service missing"
    result = await svc.compare(alice, ["same-prefix-b", "missing", "same-prefix-a"])
    assert [r.run_id for r in result.roles] == ["A", "B", "missing"]
    assert [r.profile_supported_score for r in result.roles] == [100, 100, 0]
    assert len(set(seen)) == 1 and seen[0][1] == date(2026, 6, 30)
    assert result.profile_snapshot_fingerprint
    assert result.usage["operation"] == "comparison"
    assert result.usage["counters"]["llm_calls"] == 0
    assert result.usage["total_usd"] == 0
    assert not list(container.settings.app.data_root.rglob("comparison.*"))


async def test_compare_refuses_duplicate_alias_ambiguous_prefix_and_foreign(container, alice, bob):
    one = create_run(container, alice, "same-one")
    create_run(container, alice, "same-two")
    foreign = create_run(container, bob, "private")
    svc = getattr(container, "comparison", None)
    assert svc is not None, "Comparison service missing"
    for refs in (
        ["same-one"],
        ["same-one", str(one)],
        ["same", "same-two"],
        [str(foreign), "same-one"],
    ):
        with pytest.raises(InputError):
            await svc.compare(alice, refs)


async def test_recurring_complete_signature_counts_distinct_roles_preserves_instances(
    container, alice
):
    jd = job()
    jd.requirements.append(jd.requirements[0].model_copy(update={"id": "duplicate"}))
    create_run(container, alice, "one", jd)
    other = job().model_copy(deep=True)
    other.requirements[0] = other.requirements[0].model_copy(
        update={
            "id": "other",
            "text": "  PYTHON ",
            "keywords": ["python"],
            "importance": Importance.NICE,
        }
    )
    create_run(container, alice, "two", other)
    third = job().model_copy(deep=True)
    third.requirements[0] = third.requirements[0].model_copy(update={"text": "Python architecture"})
    create_run(container, alice, "three", third)
    svc = getattr(container, "comparison", None)
    assert svc is not None, "Comparison service missing"
    result = await svc.compare(alice, ["one", "two", "three"])
    assert len(result.recurring_gaps) == 1
    gap = result.recurring_gaps[0]
    assert gap.role_count == 2 and len(gap.instances) == 3
    assert gap.weighted_missing_points == 200
    assert {(i.run_id, i.requirement_id) for i in gap.instances} == {
        ("one", "R1"),
        ("one", "duplicate"),
        ("two", "other"),
    }
    assert not (await svc.compare(alice, ["one", "three"])).recurring_gaps


def test_comparison_cli_json_export_and_no_key(settings, tmp_path):
    from types import SimpleNamespace

    from cvopt.adapters.files import LocalFileStore
    from cvopt.context import UserContext

    c = SimpleNamespace(files=LocalFileStore(settings.app.data_root))
    user = UserContext.from_email("alice@example.com")
    create_run(c, user, "one")
    create_run(c, user, "two")
    out = tmp_path / "export"
    result = CliRunner().invoke(
        app,
        [
            "--user",
            user.user_id,
            "--data-root",
            str(settings.app.data_root),
            "run",
            "compare",
            "one",
            "two",
            "--as-of",
            "2024-01-31",
            "--json",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    value = json.loads(result.stdout)
    assert value["as_of"] == "2024-01-31"
    assert json.loads((out / "comparison.json").read_text()) == value
    assert "profile_supported_score" in (out / "comparison.csv").read_text()
    assert "Wrote" not in result.stdout


async def test_saved_target_staleness_and_legacy_unavailable(container, alice, monkeypatch):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    from cvopt.domain.provenance import content_fingerprint
    from cvopt.services.comparison import generation_context

    jd = job()
    run = create_run(container, alice, "saved", jd)
    create_run(container, alice, "legacy", jd)
    profile = await ProfileCoverageService(
        container.repos, container.runners, container.settings
    ).load(alice)
    metadata = {
        "run_id": "saved",
        "generated_at": "2026-06-30T12:00:00Z",
        "as_of": "2026-06-30",
        "config": container.settings.model_dump(mode="json"),
        "models": generation_context(container.settings)["models"],
        "prompt_versions": generation_context(container.settings)["prompt_versions"],
    }
    # Published legacy-layout artifacts: no new target judgment is permitted.
    (run / "run.json").write_text(json.dumps(metadata))
    (run / "cv.json").write_text("{}")
    (run / "report.json").write_text(
        json.dumps(
            {
                "total": 99,
                "profile_snapshot_fingerprint": profile.snapshot.fingerprint,
                "requirements_fingerprint": content_fingerprint(jd),
            }
        )
    )
    result = await container.comparison.compare(alice, ["saved", "legacy"])
    target = next(r.saved_target for r in result.roles if r.run_id == "saved")
    assert target.score == 99 and target.generated_at == metadata["generated_at"]
    assert target.stale == {"profile": False, "requirements": False, "config": False, "date": False}
    legacy = next(r.saved_target for r in result.roles if r.run_id == "legacy")
    assert legacy.score is None and all(v is None for v in legacy.stale.values())
    changed = jd.model_copy(deep=True)
    changed.requirements[0] = changed.requirements[0].model_copy(update={"text": "changed"})
    (run / "requirements.json").write_text(changed.model_dump_json())
    await container.data.create_claim(alice, type=ClaimType.SKILL, text="New", text_lang="en")
    result = await container.comparison.compare(alice, ["saved", "legacy"], as_of=date(2025, 1, 1))
    target = next(r.saved_target for r in result.roles if r.run_id == "saved")
    assert target.score == 99
    assert target.stale == {"profile": True, "requirements": True, "config": False, "date": True}


@pytest.mark.parametrize(
    "section,field,value,stale",
    [
        ("app", "log_level", "DEBUG", False),
        ("app", "data_root", "/tmp/other", False),
        ("llm", "prices", {"example": {"input": 1}}, False),
        ("scoring", "weight_must", 9, True),
        ("optimizer", "page_budget", 1, True),
        ("layout", "chars_per_line", 90, True),
        ("render", "font_size_pt", 9, True),
        ("llm", "strong_model", "different", True),
    ],
)
async def test_saved_configuration_staleness_ignores_operational_changes(
    container, alice, section, field, value, stale
):
    from cvopt.services.comparison import generation_context

    run = create_run(container, alice, "saved")
    create_run(container, alice, "other")
    config = container.settings.model_dump(mode="json")
    config[section][field] = value
    context = generation_context(container.settings)
    models = dict(context["models"])
    if section == "llm" and field == "strong_model":
        models["strong"] = value
    (run / "run.json").write_text(
        json.dumps(
            {"config": config, "models": models, "prompt_versions": context["prompt_versions"]}
        )
    )
    result = await container.comparison.compare(alice, ["saved", "other"])
    assert next(r.saved_target.stale["config"] for r in result.roles if r.name == "saved") is stale


@pytest.mark.parametrize("boundary", ["user", "runs"])
@pytest.mark.parametrize("outside", [False, True])
async def test_comparison_rejects_redirect_before_loading_profile(
    container, alice, bob, tmp_path, monkeypatch, boundary, outside
):
    foreign = create_run(container, bob, "private")
    home = container.files.user_dir(alice)
    target = tmp_path / "outside" if outside else foreign.parent
    if outside:
        target.mkdir()
    link = home / "runs"
    if boundary == "user":
        home.rmdir()
        link = home
        target = target if outside else target.parent
    link.symlink_to(target)

    async def forbidden(*args):
        pytest.fail("Redirected comparison reached profile loading")

    monkeypatch.setattr(ProfileCoverageService, "load", forbidden)
    with pytest.raises(InputError):
        await container.comparison.compare(alice, ["private", "other"])


async def test_comparison_years_union_and_must_gap_tiebreak(container, alice, monkeypatch):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    for start, end in [("2020-01", "2021-12"), ("2021-01", "2022-12")]:
        position = await repo.insert_position(
            dict(employer="Example", title="Engineer", start_month=start, end_month=end)
        )
        await container.data.create_claim(
            alice,
            type=ClaimType.EXPERIENCE,
            text="Python delivery",
            text_lang="en",
            position=position.id,
        )
    years = job("years_experience", years=4)
    create_run(container, alice, "years", years)
    create_run(container, alice, "complete", job())
    result = await container.comparison.compare(alice, ["years", "complete"])
    role = next(r for r in result.roles if r.name == "years")
    assert role.profile_supported_score == 75
    assert len(role.requirements[0].supporting_elements) == 2
    assert all(
        s.position_ref and s.start_month and s.end_month
        for s in role.requirements[0].supporting_elements
    )
    # Equal totals: losing a must requirement ranks below losing a nice requirement.
    language = job("language", language_code="en", language_level="C2").requirements[0]
    supported = job().requirements[0]
    a = job().model_copy(
        update={
            "requirements": [
                supported.model_copy(update={"id": "S", "importance": Importance.NICE}),
                language,
            ]
        }
    )
    b = job().model_copy(
        update={
            "requirements": [
                supported,
                language.model_copy(update={"id": "L", "importance": Importance.NICE}),
            ]
        }
    )
    # Use equal weights so coverage ties and must gap is the deciding metric.

    from cvopt.services.comparison import ComparisonService

    settings = container.settings.model_copy(
        update={"scoring": container.settings.scoring.model_copy(update={"weight_must": 1})}
    )
    create_run(container, alice, "a", a)
    create_run(container, alice, "b", b)
    svc = ComparisonService(
        container.runs, container.repos, container.runners, container.scoring._clock, settings
    )
    result = await svc.compare(alice, ["a", "b"])
    assert [r.name for r in result.roles] == ["b", "a"]
    assert [r.profile_supported_score for r in result.roles] == [50, 50]


async def test_comparison_pins_saved_generation_and_measures_real_judging(
    container, alice, fake_llm
):
    from cvopt.services.publication import RunPublication
    from tests.pipeline.test_publication import populate

    await container.data.create_claim(alice, type=ClaimType.SKILL, text="Python", text_lang="en")
    run = create_run(container, alice, "one")
    create_run(container, alice, "two")
    with RunPublication(run).attempt() as stage:
        populate(stage)
        (stage.path / "run.json").write_text(
            json.dumps({"run_id": "one", "generated_at": "2026-06-29T12:00:00Z"})
        )
        (stage.path / "report.json").write_text('{"total":91}')
        stage.publish()
    fake_llm.script(
        "end_turn", {"verdicts": [{"ref": "E1", "level": "semantic", "reason": "synthetic"}]}
    )
    result = await container.comparison.compare(alice, ["one", "two"])
    assert result.usage["counters"]["llm_calls"] == 1
    assert result.usage["counters"]["application_cache_hits"] >= 1
    assert result.usage["total_usd"] is None
    assert next(r.saved_target.score for r in result.roles if r.name == "one") == 91
    assert not (run / "comparison.json").exists()


async def test_csv_retains_nonrecurring_requirement_gaps(container, alice):
    import csv
    import io

    from cvopt.domain.comparison import comparison_csv

    create_run(container, alice, "one")
    create_run(container, alice, "two", job("language", language_code="de", language_level="C1"))
    result = await container.comparison.compare(alice, ["one", "two"])
    rows = list(csv.reader(io.StringIO(comparison_csv(result))))
    assert any("requirement_id" in row and "coverage" in row for row in rows)
    assert any("Python" in row and "100.0" in row for row in rows)


async def test_saved_prompt_and_provider_identity_changes_are_stale(container, alice):
    from cvopt.services.comparison import generation_context

    run = create_run(container, alice, "one")
    create_run(container, alice, "two")
    context = generation_context(container.settings)
    for change in ("prompt", "provider"):
        config = container.settings.model_dump(mode="json")
        versions = dict(context["prompt_versions"])
        if change == "prompt":
            versions["translate"] = "old-version"
        else:
            config["llm"]["provider"] = "different"
        (run / "run.json").write_text(
            json.dumps({"config": config, "models": context["models"], "prompt_versions": versions})
        )
        result = await container.comparison.compare(alice, ["one", "two"])
        assert next(r.saved_target.stale["config"] for r in result.roles if r.name == "one") is True


async def test_complete_synthetic_cli_intake_generate_discover_compare_workflow(
    settings, alice, monkeypatch, tmp_path
):
    from cvopt import bootstrap
    from cvopt.adapters.clock import FixedClock
    from tests import recordings
    from tests.conftest import TEST_NOW
    from tests.fakes import FakeLLMClient
    from tests.pipeline.test_cli import acli
    from tests.scenarios import CV, JD, LETTER
    from tests.synthetic_golden import install_synthetic_golden

    async def offline(settings, **kw):
        c = await bootstrap.build_container(
            settings, clock=FixedClock(TEST_NOW), llm=FakeLLMClient(), observer=kw.get("observer")
        )
        await recordings.load(c.repos.for_user(alice))
        install_synthetic_golden(c.runners)
        return c

    monkeypatch.setattr("cvopt.cli.common.build_container", offline)
    for path, kind in ((CV, "cv"), (LETTER, "reference_letter")):
        await acli(settings, "ingest", str(path), "--kind", kind, "--accept-all")
    await acli(settings, "run", "new", "--text", str(JD / "jd_en_nordwind.txt"))
    await acli(settings, "run", "new", "--pdf", str(CV.parent / "jd_de_alpenbank.pdf"))
    runs = json.loads(await acli(settings, "run", "list", "--json"))
    assert len(runs) == 2
    for row in runs:
        await acli(settings, "run", "generate", row["name"])
        shown = json.loads(await acli(settings, "run", "show", row["name"], "--json"))
        assert shown["published"] and shown["artifacts"]["cv.pdf"]
    out = tmp_path / "comparison-export"
    import asyncio

    def compare_cli():
        return CliRunner().invoke(
            app,
            [
                "--user",
                alice.user_id,
                "--data-root",
                str(settings.app.data_root),
                "run",
                "compare",
                *[r["name"] for r in runs],
                "--as-of",
                "2026-06-30",
                "--json",
                "--out",
                str(out),
            ],
        )

    result = await asyncio.to_thread(compare_cli)
    assert result.exit_code == 0, result.output
    value = json.loads(result.stdout)
    assert len(value["roles"]) == 2
    assert all(
        r["saved_target"]["stale"]
        == {"profile": False, "requirements": False, "config": False, "date": False}
        for r in value["roles"]
    )
    assert value == json.loads((out / "comparison.json").read_text())


async def test_partial_legacy_configuration_is_unknown_unless_known_settings_changed(
    container, alice
):
    run = create_run(container, alice, "one")
    create_run(container, alice, "two")
    config = container.settings.model_dump(mode="json")
    for changed in (False, True):
        if changed:
            config["render"]["font"] = "different"
        (run / "run.json").write_text(
            json.dumps({"config": config, "models": {}, "prompt_versions": {}})
        )
        result = await container.comparison.compare(alice, ["one", "two"])
        state = next(r.saved_target.stale["config"] for r in result.roles if r.name == "one")
        assert state is (True if changed else None)


@pytest.mark.parametrize(
    "config,models,prompts,expected",
    [
        ({"scoring": {"weight_must": 3}}, {}, {}, None),
        ({"scoring": {"weight_must": 9}}, {}, {}, True),
        ({"optimizer": {"page_budget": 2}, "render": {"font": "Lato"}}, {}, {}, None),
        ({"render": {"margin_mm": None}}, {}, {}, None),
        ({"layout": {"chars_per_line": 90}}, {}, {}, True),
        ({"scoring": {"weight_must": 3}}, {"strong": "claude-sonnet-5-5"}, {}, None),
        ({"scoring": {"weight_must": 3}}, {"fast": "different"}, {}, True),
        ({"scoring": {"weight_must": 3}}, {}, {"translate": "old-version"}, True),
    ],
)
def test_partial_config_sections_compare_only_known_identity_leaves(
    config, models, prompts, expected
):
    from cvopt.config import Settings
    from cvopt.services.comparison import _config_stale, generation_context

    metadata = {"config": config, "models": models, "prompt_versions": prompts}
    assert _config_stale(metadata, generation_context(Settings())) is expected


def test_complete_config_identity_is_equal_with_nullable_and_operational_settings():
    from cvopt.config import Settings
    from cvopt.services.comparison import _config_stale, generation_context

    settings = Settings()
    current = generation_context(settings)
    metadata = {
        "config": settings.model_dump(mode="json"),
        "models": current["models"],
        "prompt_versions": current["prompt_versions"],
    }
    metadata["config"]["app"]["log_level"] = "DEBUG"
    metadata["config"]["llm"]["prices"] = {"different": {"input": 1}}
    assert _config_stale(metadata, current) is False
    metadata["prompt_versions"] = {"translate": current["prompt_versions"]["translate"]}
    assert _config_stale(metadata, current) is None
    metadata["config"]["scoring"]["weight_must"] = 9
    assert _config_stale(metadata, current) is True
