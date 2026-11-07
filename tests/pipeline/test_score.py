"""Scoring (F5) offline, from recordings."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvopt.bootstrap import Container
from cvopt.cli.main import app
from cvopt.context import UserContext
from cvopt.services.scoring import Scored
from tests.fakes import FakeLLMClient
from tests.scenarios import HANDWRITTEN_CV, check_handwritten, score_handwritten


async def test_score_handwritten_cv(
    golden: Container, alice: UserContext, fake_llm: FakeLLMClient
) -> None:
    first, second, run = await score_handwritten(golden, alice)
    check_handwritten(first, second)
    assert not fake_llm.calls  # everything came from the cache
    assert isinstance(first, Scored) and str(first.as_of) == "2026-06-30"


async def test_as_of_comes_from_run_json(
    golden: Container, alice: UserContext, tmp_path: Path
) -> None:
    _, _, run = await score_handwritten(golden, alice)
    cv_in_run = run.path / "cv.json"
    cv_in_run.write_text(HANDWRITTEN_CV.read_text())
    info = json.loads((run.path / "run.json").read_text())
    info["as_of"] = "2030-01-31"
    (run.path / "run.json").write_text(json.dumps(info))
    s = await golden.scoring.score(alice, cv_in_run, run.path / "requirements.json")
    assert str(s.as_of) == "2030-01-31"
    old = [e for e in s.elements if e.id.startswith("/work/2/")]
    assert old and all(e.placement == 0.85 for e in old)


def test_score_cli_rejects_bad_files(tmp_path: Path) -> None:
    bad = tmp_path / "cv.json"
    bad.write_text("{not json")
    result = CliRunner().invoke(
        app,
        [
            "--user",
            "a@example.com",
            "--data-root",
            str(tmp_path),
            "score",
            str(bad),
            "--requirements",
            str(bad),
        ],
    )
    assert result.exit_code == 1 and "Cannot read CV" in result.output


async def test_external_legacy_and_changed_cv_reports_never_borrow_profile_coverage(
    container, alice, tmp_path
):
    from datetime import date

    from cvopt.domain.cv import JSONResume
    from cvopt.domain.jd import JobDescription, Requirement
    from cvopt.domain.provenance import ProfileSnapshot, content_fingerprint, snapshot_fingerprint
    from cvopt.domain.records import Profile
    from cvopt.services.scoring import scoring_context, standalone_report

    day = date(2026, 6, 30)
    cv = JSONResume.model_validate({"languages": [{"language": "English", "fluency": "B1"}]})
    jd = JobDescription(
        title="Example",
        company="",
        language="en",
        requirements=[
            Requirement(
                id="R1",
                text="English C2",
                category="language",
                importance="must",
                keywords=[],
                synonyms=[],
                language_code="en",
                language_level="C2",
            )
        ],
        logistics=[],
    )
    cp, rp = tmp_path / "cv.json", tmp_path / "requirements.json"
    cp.write_text(cv.model_dump_json())
    rp.write_text(jd.model_dump_json())
    original = await container.scoring.score(alice, cp, rp, day)
    assert standalone_report(original, None, 6).profile_supported_score is None
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
    )
    snapshot = snapshot.model_copy(update={"fingerprint": snapshot_fingerprint(snapshot)})
    report = standalone_report(original, None, 6).model_copy(
        update={
            "profile_supported_score": 40.0,
            "target_minus_profile": original.result.total - 40,
            "profile_snapshot_fingerprint": snapshot.fingerprint,
            "target_cv_fingerprint": content_fingerprint(cv),
            "requirements_fingerprint": content_fingerprint(jd),
            "scoring_context": scoring_context(container.settings, day),
        }
    )
    (tmp_path / "report.json").write_text(report.model_dump_json())
    (tmp_path / "profile-snapshot.json").write_text(snapshot.model_dump_json())
    assert (
        standalone_report(
            await container.scoring.score(alice, cp, rp, day), None, 6
        ).profile_supported_score
        == 40
    )
    cp.write_text(cv.model_copy(update={"meta": {"changed": "external edit"}}).model_dump_json())
    assert (
        standalone_report(
            await container.scoring.score(alice, cp, rp, day), None, 6
        ).profile_supported_score
        is None
    )
    cp.write_text(cv.model_dump_json())
    (tmp_path / "profile-snapshot.json").write_text(
        snapshot.model_copy(update={"schema_version": 1}).model_dump_json()
    )
    assert (
        standalone_report(
            await container.scoring.score(alice, cp, rp, day), None, 6
        ).profile_supported_score
        is None
    )
    (tmp_path / "profile-snapshot.json").write_text(
        snapshot.model_copy(update={"profile": Profile(name="changed")}).model_dump_json()
    )
    assert (
        standalone_report(
            await container.scoring.score(alice, cp, rp, day), None, 6
        ).profile_supported_score
        is None
    )


async def test_changed_semantic_target_refuses_historical_profile_explanations(
    container, alice, tmp_path, monkeypatch
):
    from datetime import date

    from cvopt.domain.cv import JSONResume
    from cvopt.domain.enums import MatchLevel
    from cvopt.domain.jd import JobDescription, Requirement
    from cvopt.domain.provenance import ProfileSnapshot, content_fingerprint, snapshot_fingerprint
    from cvopt.domain.records import Profile
    from cvopt.llm.judge import Judge
    from cvopt.services.scoring import scoring_context, standalone_report

    level = MatchLevel.NONE

    async def verdicts(self, req, texts):
        return {text: level for text in texts}

    monkeypatch.setattr(Judge, "verdicts", verdicts)
    day = date(2026, 6, 30)
    cv = JSONResume.model_validate({"skills": [{"keywords": ["Python"]}]})
    jd = JobDescription(
        title="Example",
        company="",
        language="en",
        requirements=[
            Requirement(
                id="R1",
                text="Python",
                category="hard_skill",
                importance="must",
                keywords=["Python"],
                synonyms=[],
            )
        ],
        logistics=[],
    )
    cp, rp = tmp_path / "cv.json", tmp_path / "requirements.json"
    cp.write_text(cv.model_dump_json())
    rp.write_text(jd.model_dump_json())
    original = await container.scoring.score(alice, cp, rp, day)
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
    )
    snapshot = snapshot.model_copy(update={"fingerprint": snapshot_fingerprint(snapshot)})
    historical = standalone_report(original, None, 6).model_copy(
        update={
            "profile_supported_score": 40.0,
            "target_minus_profile": -40.0,
            "profile_snapshot_fingerprint": snapshot.fingerprint,
            "target_cv_fingerprint": content_fingerprint(cv),
            "requirements_fingerprint": content_fingerprint(jd),
            "scoring_context": scoring_context(container.settings, day),
        }
    )
    (tmp_path / "report.json").write_text(historical.model_dump_json())
    (tmp_path / "profile-snapshot.json").write_text(snapshot.model_dump_json())
    level = MatchLevel.SEMANTIC
    changed = await container.scoring.score(alice, cp, rp, day)
    assert changed.result.total == 70
    report = standalone_report(changed, None, 6)
    assert changed.matching_report is None
    assert report.profile_supported_score is None and report.target_minus_profile is None
    assert report.total == 70 and report.requirements[0].target_coverage == 0.7
    assert report.requirements[0].target_statements[0].element_id == "/skills/0/keywords/0"


@pytest.mark.parametrize("change", ["new_relation", "new_winner"])
async def test_equal_total_changed_relations_or_winners_refuse_historical_reuse(
    container, alice, tmp_path, monkeypatch, change
):
    from datetime import date

    from cvopt.domain.cv import JSONResume
    from cvopt.domain.enums import MatchLevel
    from cvopt.domain.jd import JobDescription, Requirement
    from cvopt.domain.provenance import ProfileSnapshot, content_fingerprint, snapshot_fingerprint
    from cvopt.domain.records import Profile
    from cvopt.llm.judge import Judge
    from cvopt.services.scoring import scoring_context, standalone_report

    changed = False

    async def verdicts(self, req, texts):
        if change == "new_relation":
            return {
                t: MatchLevel.SEMANTIC if t == "Alpha" or changed else MatchLevel.NONE
                for t in texts
            }
        return {
            t: MatchLevel.SEMANTIC if (t == "Alpha") != changed else MatchLevel.PARTIAL
            for t in texts
        }

    monkeypatch.setattr(Judge, "verdicts", verdicts)
    day = date(2026, 6, 30)
    cv = JSONResume.model_validate({"skills": [{"keywords": ["Alpha", "Beta"]}]})
    jd = JobDescription(
        title="Example",
        company="",
        language="en",
        requirements=[
            Requirement(
                id="R1",
                text="Programming",
                category="hard_skill",
                importance="must",
                keywords=[],
                synonyms=[],
            )
        ],
        logistics=[],
    )
    cp, rp = tmp_path / "cv.json", tmp_path / "requirements.json"
    cp.write_text(cv.model_dump_json())
    rp.write_text(jd.model_dump_json())
    original = await container.scoring.score(alice, cp, rp, day)
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
    )
    snapshot = snapshot.model_copy(update={"fingerprint": snapshot_fingerprint(snapshot)})
    historical = standalone_report(original, None, 6).model_copy(
        update={
            "profile_supported_score": 40.0,
            "target_minus_profile": original.result.total - 40,
            "profile_snapshot_fingerprint": snapshot.fingerprint,
            "target_cv_fingerprint": content_fingerprint(cv),
            "requirements_fingerprint": content_fingerprint(jd),
            "scoring_context": scoring_context(container.settings, day),
        }
    )
    (tmp_path / "report.json").write_text(historical.model_dump_json())
    (tmp_path / "profile-snapshot.json").write_text(snapshot.model_dump_json())
    changed = True
    current = await container.scoring.score(alice, cp, rp, day)
    assert current.result.total == original.result.total == 42
    assert current.matching_report is None
    report = standalone_report(current, None, 6)
    assert report.profile_supported_score is None and report.target_minus_profile is None
    assert len(report.requirements[0].target_statements) == 2
    expected = 2 if change == "new_relation" else 1
    assert sum(s.winning_share > 0 for s in report.requirements[0].target_statements) == expected
