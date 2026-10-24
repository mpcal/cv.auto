"""Records can be created, listed, edited and deleted through the CLI."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvopt.cli.main import app

runner = CliRunner()


@pytest.fixture
def cli(tmp_path: Path):  # type: ignore[no-untyped-def]
    def run(*args: str, code: int = 0) -> str:
        result = runner.invoke(
            app, ["--user", "a@example.com", "--data-root", str(tmp_path), *args]
        )
        assert result.exit_code == code, result.output
        return result.output

    return run


def created_id(output: str) -> str:
    m = re.search(r"Created \w+ (\w{26})", output)
    assert m, output
    return m.group(1)


def test_position_crud(cli) -> None:  # type: ignore[no-untyped-def]
    pid = created_id(
        cli(
            "position",
            "add",
            "--employer",
            "Acme",
            "--title",
            "Dev",
            "--start",
            "2020-01",
            "--location",
            "Zurich",
        )
    )
    assert "Acme" in cli("position", "list")
    cli("position", "edit", pid[-6:], "--title", "Lead", "--end", "2024-06")
    [p] = json.loads(cli("position", "list", "--json"))
    assert (p["title"], p["end_month"], p["employer_location"]) == ("Lead", "2024-06", "Zurich")
    cli("position", "edit", pid, "--end", "current")
    assert json.loads(cli("position", "list", "--json"))[0]["end_month"] is None
    cli("position", "rm", pid)
    assert json.loads(cli("position", "list", "--json")) == []


def test_fact_records_crud(cli) -> None:  # type: ignore[no-untyped-def]
    eid = created_id(
        cli(
            "education",
            "add",
            "--institution",
            "ETH",
            "--degree",
            "MSc",
            "--area",
            "CS",
            "--start",
            "2014-09",
            "--end",
            "2016-08",
        )
    )
    [e] = json.loads(cli("education", "list", "--json"))
    assert e["degree_level"] == "master"
    cli("education", "edit", eid, "--grade", "5.5")
    assert json.loads(cli("education", "list", "--json"))[0]["grade"] == "5.5"

    lid = created_id(cli("language", "add", "--language", "Deutsch", "--level", "fließend"))
    assert json.loads(cli("language", "list", "--json"))[0] == {
        "id": lid,
        "language_code": "de",
        "level": "C1",
    }
    cli("language", "edit", lid, "--level", "native")
    cli("language", "add", "--language", "Klingon", "--level", "C1", code=1)

    cid = created_id(cli("certificate", "add", "--name", "AWS SA", "--date", "2023-02"))
    cli("certificate", "edit", cid, "--issuer", "Amazon")
    assert json.loads(cli("certificate", "list", "--json"))[0]["issuer"] == "Amazon"

    for group, rid in [("education", eid), ("language", lid), ("certificate", cid)]:
        cli(group, "rm", rid)
        assert json.loads(cli(group, "list", "--json")) == []


def test_claims_and_evidence(cli) -> None:  # type: ignore[no-untyped-def]
    pid = created_id(
        cli("position", "add", "--employer", "Acme", "--title", "Dev", "--start", "2020-01")
    )
    cid = created_id(
        cli(
            "claim",
            "add",
            "--type",
            "skill",
            "--text",
            "Kubernetes",
            "--position",
            pid,
            "--keywords",
            "kubernetes, k8s",
            "--learnable",
        )
    )
    [c] = json.loads(cli("claim", "list", "--json"))
    assert (c["provability"], c["status"], c["keywords"]) == (
        "low",
        "confirmed",
        ["kubernetes", "k8s"],
    )
    assert c["evidence"][0]["kind"] == "learnable_declared"

    out = cli("evidence", "add", cid, "--kind", "reference_person", "--reference", "Carol")
    assert "high" in out
    assert json.loads(cli("claim", "list", "--provability", "high", "--json"))[0]["id"] == cid
    assert json.loads(cli("claim", "list", "--position", pid[-8:], "--json"))
    assert json.loads(cli("claim", "list", "--type", "achievement", "--json")) == []

    cli("claim", "edit", cid, "--text", "Kubernetes operations", "--position", "none")
    assert json.loads(cli("claim", "list", "--json"))[0]["position_id"] is None

    out = cli("position", "rm", pid)  # claim was untied, so this succeeds
    cli("claim", "rm", cid)
    assert json.loads(cli("claim", "list", "--json")) == []


def test_position_with_claims_cannot_be_deleted(cli) -> None:  # type: ignore[no-untyped-def]
    pid = created_id(
        cli("position", "add", "--employer", "Acme", "--title", "Dev", "--start", "2020-01")
    )
    cli("claim", "add", "--type", "experience", "--text", "Led a team", "--position", pid)
    assert "claim" in cli("position", "rm", pid, code=1)
    assert len(json.loads(cli("position", "list", "--json"))) == 1


def test_profile_and_errors(cli) -> None:  # type: ignore[no-untyped-def]
    cli("profile", "set", "name", "Alice Example")
    assert json.loads(cli("profile", "show", "--json"))["name"] == "Alice Example"
    cli("profile", "set", "nickname", "x", code=1)
    cli("position", "rm", "doesnotexist", code=1)
    cli("position", "add", "--employer", "A", "--title", "T", "--start", "2020-13", code=1)
    cli("document", "list")


def test_commands_need_a_user(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--data-root", str(tmp_path), "position", "list"])
    assert result.exit_code == 1


def test_export_and_delete_user(cli, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cli("position", "add", "--employer", "Acme", "--title", "Dev", "--start", "2020-01")
    archive = tmp_path / "out" / "export.zip"
    assert "Wrote" in cli("export", "-o", str(archive))
    assert archive.is_file()
    cli("delete-user", "--confirm", "someone@else.com", code=1)
    assert len(json.loads(cli("position", "list", "--json"))) == 1
    assert "deleted" in cli("delete-user", "--confirm", "a@example.com")
    assert json.loads(cli("position", "list", "--json")) == []
