"""Golden ingestion scenario, shared by the offline pipeline tests and the live suite."""

from dataclasses import dataclass
from pathlib import Path

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import DocumentKind, ProposalAction
from cvopt.domain.review import ProposalItem
from cvopt.services.data import ClaimWithEvidence
from cvopt.services.jd import RunResult

GOLDEN = Path(__file__).parent / "golden"
CV = GOLDEN / "alice_cv.pdf"
LETTER = GOLDEN / "alice_reference_bergwerk.pdf"


@dataclass
class Ingested:
    cv_items: list[ProposalItem]
    letter_items: list[ProposalItem]
    claims: list[ClaimWithEvidence]


async def ingest_and_apply(
    c: Container, user: UserContext, path: Path, kind: DocumentKind
) -> list[ProposalItem]:
    res = await c.ingestion.ingest_document(user, path, kind)
    assert res.batch_id, f"{path.name} produced no proposals"
    items = await c.ingestion.list_items(user, res.batch_id)
    await c.ingestion.accept_all(user, res.batch_id)
    await c.ingestion.apply_batch(user, res.batch_id)
    return items


async def ingest_golden(c: Container, user: UserContext) -> Ingested:
    cv_items = await ingest_and_apply(c, user, CV, DocumentKind.CV)
    letter_items = await ingest_and_apply(c, user, LETTER, DocumentKind.REFERENCE_LETTER)
    return Ingested(cv_items, letter_items, await c.data.list_claims(user))


def check_golden(c: Container, result: Ingested) -> None:
    """The acceptance checks for M4 (P1 §10), asserting meaning, not wording."""

    def with_keyword(kw: str) -> list[ClaimWithEvidence]:
        return [
            r
            for r in result.claims
            if kw.lower() in {k.lower() for k in r.claim.keywords} and r.claim.type.value != "skill"
        ]

    # The CV yields its positions and claims.
    created = [i for i in result.cv_items if i.action is ProposalAction.CREATE_POSITION]
    assert len(created) == 3
    texts = " ".join(r.claim.text for r in result.claims)
    for fact in ("18%", "40%", "LightGBM", "Tableau"):
        assert fact in texts, fact

    # The PostgreSQL migration is in both documents: one claim, two evidence entries, high.
    [migration] = with_keyword("PostgreSQL")
    assert sorted(e.kind.value for e in migration.evidence) == ["own_cv", "reference_letter"]
    assert migration.claim.provability.value == "high"
    [churn] = with_keyword("scikit-learn")
    assert churn.claim.provability.value == "high"

    # The coded-satisfactory passage on client presentations is a note, not evidence.
    notes = [
        i.payload.text
        for i in result.letter_items  # type: ignore[union-attr]
        if i.action is ProposalAction.NOTE
    ]
    assert any("Präsentation" in n for n in notes), notes
    excerpts = [e.excerpt for r in result.claims for e in r.evidence]
    assert not any("Präsentation" in x for x in excerpts)
    [presenting] = with_keyword("stakeholder management")
    assert [e.kind.value for e in presenting.evidence] == ["own_cv"]
    assert not any(i.action is ProposalAction.CREATE_CLAIM for i in result.letter_items)


# --- JD intake (M5) ----------------------------------------------------------------

JD = GOLDEN / "jd"
JD_URL = "https://jobs.fjordlight.example/ml-engineer"


async def golden_runs(c: Container, user: UserContext) -> dict[str, RunResult]:
    return {
        "en": await c.jd.create_run(user, text=JD / "jd_en_nordwind.txt"),
        "de": await c.jd.create_run(user, pdf=GOLDEN / "jd_de_alpenbank.pdf"),
        "phd": await c.jd.create_run(user, text=JD / "jd_en_research_phd.txt"),
        "html": await c.jd.create_run(user, url=JD_URL),
    }


def check_golden_runs(runs: dict[str, RunResult]) -> None:
    from cvopt.domain.enums import DegreeLevel, Importance
    from cvopt.domain.enums import RequirementCategory as C

    for key, run in runs.items():
        reqs = run.jd.requirements
        assert len(reqs) >= 4, key
        assert [r.id for r in reqs] == [f"R{i:02d}" for i in range(1, len(reqs) + 1)]
        for name in ("jd.txt", "requirements.json", "run.json"):
            assert (run.path / name).is_file(), (key, name)

    def cat(key: str, category: C) -> list:  # type: ignore[type-arg]
        return [r for r in runs[key].jd.requirements if r.category is category]

    en = runs["en"].jd
    assert (en.language, en.company) == ("en", "Nordwind Retail AG")
    assert any(r.years == 5 for r in cat("en", C.YEARS_EXPERIENCE))
    assert any(r.language_code == "en" and r.language_level == "C1" for r in cat("en", C.LANGUAGE))
    assert any(
        r.language_code == "de" and r.importance is Importance.NICE for r in cat("en", C.LANGUAGE)
    )
    assert any(r.degree_level is DegreeLevel.MASTER for r in cat("en", C.EDUCATION))
    assert any("permit" in item.lower() for item in en.logistics)

    de = runs["de"].jd
    assert (de.language, de.company) == ("de", "Alpenbank AG")
    assert any(
        r.language_code == "de" and r.language_level in ("C1", "C2", "native")
        for r in cat("de", C.LANGUAGE)
    )
    assert any(r.years == 3 for r in cat("de", C.YEARS_EXPERIENCE))

    assert any(
        r.degree_level is DegreeLevel.DOCTORATE and r.importance is Importance.MUST
        for r in cat("phd", C.EDUCATION)
    )

    # The page's embedded instruction is data: no Klingon, company unchanged, musts stay musts.
    html = runs["html"].jd
    assert html.company == "Fjordlight Labs"
    assert not any("klingon" in r.text.lower() for r in html.requirements)
    assert any(
        "PyTorch" in r.keywords and r.importance is Importance.MUST for r in html.requirements
    )
    assert (runs["html"].path / "jd-source.html").is_file()


# --- scoring (M6) ------------------------------------------------------------------

HANDWRITTEN_CV = GOLDEN / "cv_handwritten.json"


async def score_handwritten(c: Container, user: UserContext) -> tuple[object, object, RunResult]:
    run = await c.jd.create_run(user, text=JD / "jd_en_nordwind.txt")
    first = await c.scoring.score(user, HANDWRITTEN_CV, run.path / "requirements.json")
    second = await c.scoring.score(user, HANDWRITTEN_CV, run.path / "requirements.json")
    return first, second, run


def check_handwritten(first: object, second: object) -> None:
    import math

    from cvopt.services.scoring import Scored

    assert isinstance(first, Scored) and isinstance(second, Scored)
    res = first.result
    gaps = sum(r.gap_points for r in res.requirements)
    assert math.isclose(sum(res.impacts.values()) + gaps, 100)
    assert 40 < res.total < 100
    assert second.result == res  # scoring twice gives identical results
    by_text = {r.text: r for r in first.jd.requirements}
    years = next(r for r in first.jd.requirements if r.years == 5)
    covered = {r.requirement_id: r.coverage for r in res.requirements}
    assert covered[years.id] == 1.0  # over 9 years of Python work
    assert any(covered[r.id] >= 0.85 for t, r in by_text.items() if "SQL" in t)  # older position


# --- generation and report (M8) ------------------------------------------------------

GENERATION_JDS = {
    "en": ("text", JD / "jd_en_nordwind.txt"),
    "de": ("pdf", GOLDEN / "jd_de_alpenbank.pdf"),
    "phd": ("text", JD / "jd_en_research_phd.txt"),
}


async def generate_golden(c: Container, user: UserContext) -> dict[str, Path]:
    """Alice's CV and letter ingested, then one generated run per golden JD."""
    await ingest_golden(c, user)
    out = {}
    for key, (kind, path) in GENERATION_JDS.items():
        run = await c.jd.create_run(user, **{kind: path})  # type: ignore[arg-type]
        await c.generation.generate(user, run.name)
        out[key] = run.path
    return out


async def check_generation(c: Container, user: UserContext, run_dir: Path) -> None:
    """Every generation and report criterion of P1 §10, checked independently of the
    pipeline's own validator."""
    import json
    import math
    import re
    from datetime import date

    import jsonschema
    from pypdf import PdfReader

    from cvopt.adapters.language import LinguaDetector
    from cvopt.domain.cv import JSONResume, Placement, parse_elements
    from cvopt.domain.enums import ElementKind, Fidelity, Provability, RowKind
    from cvopt.domain.records import Claim
    from cvopt.domain.rules import (
        cefr_level,
        language_code,
        numbers,
        statement_provability,
    )
    from cvopt.services.render import build_render_model
    from cvopt.services.scoring import load_requirements

    def flat(text: str) -> str:
        """Whitespace collapsed; a line break after a hyphen is no space."""
        return re.sub(r"-\s+", "-", re.sub(r"\s+", " ", text))

    def load(name: str) -> object:
        return json.loads((run_dir / name).read_text())

    cv_raw = load("cv.json")
    assert isinstance(cv_raw, dict)
    jd = load_requirements(run_dir / "requirements.json")
    annotations = load("annotations.json")
    report = load("report.json")
    candidates = {x["id"]: x for x in load("candidates.json")}  # type: ignore[union-attr]
    assert isinstance(annotations, list) and isinstance(report, dict)

    # cv.json validates against the JSON Resume schema.
    from importlib.resources import files

    schema = json.loads(files("cvopt.resources").joinpath("jsonresume.schema.json").read_text())
    jsonschema.validate(cv_raw, schema)

    # Every stored position appears exactly once, ordered as in P1 §6.2, fields identical.
    positions = await c.data.list_positions(user)
    current = sorted(
        (p for p in positions if p.end_month is None), key=lambda p: p.start_month, reverse=True
    )
    ended = sorted(
        (p for p in positions if p.end_month is not None),
        key=lambda p: (p.end_month or "", p.start_month),
        reverse=True,
    )
    ordered = current + ended
    work = cv_raw["work"]
    assert [
        (w["name"], w["position"], w.get("location", ""), w["startDate"], w.get("endDate"))
        for w in work
    ] == [(p.employer, p.title, p.employer_location, p.start_month, p.end_month) for p in ordered]

    # Education, languages and certificates identical to the database.
    edu = await c.data.list_education(user)
    assert [
        (
            e["institution"],
            e.get("area", ""),
            e.get("studyType", ""),
            e.get("startDate"),
            e.get("endDate"),
        )
        for e in cv_raw.get("education", [])
    ] == [(e.institution, e.area, e.degree_type, e.start_month, e.end_month) for e in edu]
    langs = await c.data.list_languages(user)
    assert sorted(
        (language_code(lg["language"]), cefr_level(lg["fluency"]))
        for lg in cv_raw.get("languages", [])
    ) == sorted((lg.language_code, lg.level) for lg in langs)
    certs = await c.data.list_certificates(user)
    assert [
        (x["name"], x.get("issuer", ""), x.get("date")) for x in cv_raw.get("certificates", [])
    ] == [(x.name, x.issuer, x.issued_month) for x in certs]

    # Every statement has exactly one annotation and every annotation points to a statement.
    cv = JSONResume.model_validate(cv_raw)
    statement_kinds = {
        ElementKind.HEADLINE,
        ElementKind.SUMMARY,
        ElementKind.HIGHLIGHT,
        ElementKind.SKILL,
    }
    elements = [
        e
        for e in parse_elements(cv, date(2026, 6, 30), jd.language, Placement())
        if e.kind in statement_kinds
    ]
    keys = []
    for a in annotations:
        keys.append(a["pointer"] + (f"#{a['sentence']}" if a["sentence"] is not None else ""))
    assert sorted(keys) == sorted(e.id for e in elements)
    texts = {e.id: e.text for e in elements}
    for a, k in zip(annotations, keys, strict=True):
        assert a["text"] == texts[k]

    claims = {r.claim.id: r.claim for r in await c.data.list_claims(user)}
    selected = [candidates[cid] for cid in candidates if candidates[cid]["status"] == "selected"]
    assert len(selected) == len(annotations)
    for a in annotations:
        sources: list[Claim] = [claims[s] for s in a["source_claim_ids"]]
        fid = Fidelity(a["fidelity"])
        cand = next(
            x
            for x in selected
            if x["text"] == a["text"] and x["source_claim_ids"] == a["source_claim_ids"]
        )
        # No exaggerated statement adds a number; no invented statement contains a figure.
        if fid is Fidelity.INVENTED:
            assert not re.search(r"\d", a["text"]), a["text"]
            assert not sources
        else:
            assert sources, a["text"]
            assert numbers(a["text"]) <= numbers(" ".join(s.text for s in sources)), a["text"]
        # Exaggerated: has sources and beats its best rephrased alternative by >= 1.0 pp.
        if fid is Fidelity.EXAGGERATED:
            assert cand["alternative_gain"] is not None
            assert cand["gain"] >= cand["alternative_gain"] + 1.0 - 1e-9
        # Provability matches the table of P1 §7.
        expected = statement_provability(
            fid, [s.provability for s in sources] or [Provability.NONE], inferable=cand["inferable"]
        )
        assert a["provability"] == expected.value, (a["text"], a["provability"], expected)
    # Headline: one statement; summary: 2–4 sentences (P1 §6.3).
    n_summary = sum(e.kind is ElementKind.SUMMARY for e in elements)
    assert 2 <= n_summary <= c.settings.optimizer.summary_max_sentences, n_summary
    assert sum(e.kind is ElementKind.HEADLINE for e in elements) == 1
    # Real evidence first: no invention for a requirement a sourced candidate matches at
    # "semantic" (0.6) or better.
    for x in selected:
        if x["fidelity"] == "invented":
            r = x["assertion_key"]
            assert not any(
                y["source_claim_ids"] and y["judgements"].get(r, 0) >= 0.6
                for y in candidates.values()
            ), (r, x["text"])
    # No invented assertion appears twice.
    keys_used = [x["assertion_key"] for x in selected if x["assertion_key"]]
    assert len(keys_used) == len(set(keys_used))

    # The rendered CV fits the page budget, and its language is the JD's language.
    model = build_render_model(cv, c.settings, lang=jd.language)
    clean = c.render.render_model(model, run_dir / "clean-check.pdf")
    assert clean.pages <= c.settings.optimizer.page_budget
    prose = " ".join(e.text for e in elements if e.kind is not ElementKind.SKILL)
    assert LinguaDetector().detect(prose) == jd.language

    # Text extracted from either PDF contains every statement, in reading order.
    for pdf in (clean.path, run_dir / "cv-annotated.pdf"):
        text = flat(" ".join(p.extract_text() or "" for p in PdfReader(pdf).pages))
        pos = 0
        for e in elements:
            found = text.find(flat(e.text), pos)
            assert found >= 0, (pdf.name, e.text)
            pos = found + len(flat(e.text))

    # Report: in-CV impacts sum to S; S plus gap points is 100; every requirement with
    # coverage below 1 is a gap row; scoring twice gives identical results.
    rows = report["rows"]
    in_cv = sum(r["impact"] for r in rows if r["kind"] == RowKind.IN_CV.value)
    gaps = sum(r["impact"] for r in rows if r["kind"] == RowKind.GAP.value)
    assert math.isclose(in_cv, report["total"], abs_tol=0.1)
    assert math.isclose(report["total"] + gaps, 100, abs_tol=0.1)
    first = await c.scoring.score(user, run_dir / "cv.json", run_dir / "requirements.json")
    second = await c.scoring.score(user, run_dir / "cv.json", run_dir / "requirements.json")
    assert first.result == second.result
    assert math.isclose(first.result.total, report["total"], abs_tol=1e-6)
    gap_reqs = {rid for r in rows if r["kind"] == RowKind.GAP.value for rid in r["requirements"]}
    assert gap_reqs == {r.requirement_id for r in first.result.requirements if r.coverage < 1}
    (run_dir / "clean-check.pdf").unlink()
