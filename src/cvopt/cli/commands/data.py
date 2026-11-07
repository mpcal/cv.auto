"""Data commands (F2): profile, positions, education, languages, certificates, awards,
claims, evidence and documents."""

from typing import Annotated, Any

import typer

from cvopt.bootstrap import Container
from cvopt.cli.common import (
    console,
    end_month,
    given,
    print_json,
    print_records,
    run_async,
    short,
    split_list,
    stderr_console,
)
from cvopt.cli.review import review_batch
from cvopt.context import UserContext
from cvopt.domain.enums import ClaimStatus, ClaimType, DegreeLevel, EvidenceKind, Provability
from cvopt.domain.rules import cefr_level, degree_level, language_code
from cvopt.errors import InputError

JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")]
Opt = typer.Option

profile_app = typer.Typer(help="Show and edit the profile.", no_args_is_help=True)
position_app = typer.Typer(help="Manage positions.", no_args_is_help=True)
education_app = typer.Typer(help="Manage education entries.", no_args_is_help=True)
language_app = typer.Typer(help="Manage languages.", no_args_is_help=True)
certificate_app = typer.Typer(help="Manage certificates.", no_args_is_help=True)
award_app = typer.Typer(help="Manage awards.", no_args_is_help=True)
claim_app = typer.Typer(help="Manage claims.", no_args_is_help=True)
evidence_app = typer.Typer(help="Add and remove evidence.", no_args_is_help=True)
document_app = typer.Typer(help="List and delete source documents.", no_args_is_help=True)

APPS = {
    "profile": profile_app,
    "position": position_app,
    "education": education_app,
    "language": language_app,
    "certificate": certificate_app,
    "award": award_app,
    "claim": claim_app,
    "evidence": evidence_app,
    "document": document_app,
}


def created(kind: str, id_: str) -> None:
    console.print(f"Created {kind} {id_}", highlight=False)


# --- profile -------------------------------------------------------------------


@profile_app.command("show")
def profile_show(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    p = run_async(ctx, lambda c, u: c.data.get_profile(u), writes=False)
    if as_json:
        print_json(p.model_dump(mode="json"))
        return
    for field, value in p.model_dump(mode="json").items():
        console.print(f"[bold]{field}[/bold]: {value}", highlight=False)


@profile_app.command("set")
def profile_set(ctx: typer.Context, field: str, value: str) -> None:
    run_async(ctx, lambda c, u: c.data.update_profile(u, {field: value}), writes=True)
    console.print(f"Set {field}")


# --- positions -----------------------------------------------------------------

POSITION_COLUMNS = ["id", "employer", "title", "employer_location", "start_month", "end_month"]


@position_app.command("list")
def position_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    rows = run_async(ctx, lambda c, u: c.data.list_positions(u), writes=False)
    print_records("Positions", rows, POSITION_COLUMNS, as_json=as_json)


@position_app.command("add")
def position_add(
    ctx: typer.Context,
    employer: Annotated[str, Opt()],
    title: Annotated[str, Opt()],
    start: Annotated[str, Opt(help="YYYY-MM")],
    end: Annotated[str, Opt(help="YYYY-MM or 'current'")] = "current",
    location: Annotated[str, Opt()] = "",
) -> None:
    fields = {
        "employer": employer,
        "title": title,
        "start_month": start,
        "employer_location": location,
        **end_month(end),
    }
    p = run_async(ctx, lambda c, u: c.data.create_position(u, fields), writes=True)
    created("position", p.id)


@position_app.command("edit")
def position_edit(
    ctx: typer.Context,
    id: str,
    employer: Annotated[str | None, Opt()] = None,
    title: Annotated[str | None, Opt()] = None,
    start: Annotated[str | None, Opt(help="YYYY-MM")] = None,
    end: Annotated[str | None, Opt(help="YYYY-MM or 'current'")] = None,
    location: Annotated[str | None, Opt()] = None,
) -> None:
    fields = {
        **given(employer=employer, title=title, start_month=start, employer_location=location),
        **end_month(end),
    }
    p = run_async(ctx, lambda c, u: c.data.update_position(u, id, fields), writes=True)
    console.print(f"Updated position {short(p.id)}")


@position_app.command("rm")
def position_rm(ctx: typer.Context, id: str) -> None:
    rid = run_async(ctx, lambda c, u: c.data.delete_position(u, id), writes=True)
    console.print(f"Deleted position {short(rid)}")


# --- education -----------------------------------------------------------------

EDUCATION_COLUMNS = [
    "id",
    "institution",
    "area",
    "degree_type",
    "degree_level",
    "start_month",
    "end_month",
    "grade",
    "summary",
    "highlights",
    "links",
]


def education_links(values: list[str] | None) -> list[dict[str, str]] | None:
    if values is None:
        return None
    links = []
    for value in values:
        label, separator, url = value.partition("=")
        if not separator or not label.strip() or not url.strip():
            raise InputError("Use --link 'Label=https://example.org/page'.")
        links.append({"label": label.strip(), "url": url.strip()})
    return links


@education_app.command("list")
def education_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    rows = run_async(ctx, lambda c, u: c.data.list_education(u), writes=False)
    print_records("Education", rows, EDUCATION_COLUMNS, as_json=as_json)


@education_app.command("add")
def education_add(
    ctx: typer.Context,
    institution: Annotated[str, Opt()],
    degree: Annotated[str, Opt(help="Degree as written, e.g. 'MSc'.")],
    area: Annotated[str, Opt()] = "",
    level: Annotated[DegreeLevel | None, Opt(help="Default: derived from --degree.")] = None,
    start: Annotated[str | None, Opt(help="YYYY-MM")] = None,
    end: Annotated[str | None, Opt(help="YYYY-MM")] = None,
    grade: Annotated[str, Opt()] = "",
    summary: Annotated[str, Opt(help="Department, group, adviser or other descriptive text.")] = "",
    highlight: Annotated[
        list[str] | None, Opt(help="Education bullet; repeat for each bullet.")
    ] = None,
    link: Annotated[list[str] | None, Opt(help="Label=URL; repeat for each link.")] = None,
) -> None:
    fields = {
        "institution": institution,
        "area": area,
        "degree_type": degree,
        "degree_level": level or degree_level(degree),
        "start_month": start,
        "end_month": end,
        "grade": grade,
        "summary": summary,
        "highlights": highlight or [],
        "links": education_links(link) or [],
    }
    e = run_async(ctx, lambda c, u: c.data.create_education(u, fields), writes=True)
    created("education", e.id)


@education_app.command("edit")
def education_edit(
    ctx: typer.Context,
    id: str,
    institution: Annotated[str | None, Opt()] = None,
    degree: Annotated[str | None, Opt()] = None,
    area: Annotated[str | None, Opt()] = None,
    level: Annotated[DegreeLevel | None, Opt()] = None,
    start: Annotated[str | None, Opt()] = None,
    end: Annotated[str | None, Opt()] = None,
    grade: Annotated[str | None, Opt()] = None,
    summary: Annotated[str | None, Opt()] = None,
    highlight: Annotated[
        list[str] | None, Opt(help="Replace bullets; repeat for each bullet.")
    ] = None,
    link: Annotated[
        list[str] | None, Opt(help="Replace links with Label=URL; repeat for each.")
    ] = None,
    clear_links: Annotated[bool, Opt(help="Remove all education links.")] = False,
) -> None:
    fields = given(
        institution=institution,
        degree_type=degree,
        area=area,
        degree_level=level,
        start_month=start,
        end_month=end,
        grade=grade,
        summary=summary,
        highlights=highlight,
        links=[] if clear_links else education_links(link),
    )
    e = run_async(ctx, lambda c, u: c.data.update_education(u, id, fields), writes=True)
    console.print(f"Updated education {short(e.id)}")


@education_app.command("refresh")
def education_refresh(
    ctx: typer.Context,
    document: Annotated[str, Opt(help="Previously imported CV, diploma or certificate id.")],
    accept_all: Annotated[bool, Opt(help="Accept and apply without prompts.")] = False,
) -> None:
    """Recover missing education details from an already imported document."""

    async def op(c: Container, u: UserContext) -> None:
        with stderr_console.status("Reading education details…"):
            result = await c.ingestion.refresh_education(u, document)
        if result.batch_id:
            await review_batch(c, u, result.batch_id, accept_all)
        else:
            console.print("Education details are already up to date.")

    run_async(ctx, op, writes=True)


@education_app.command("rm")
def education_rm(ctx: typer.Context, id: str) -> None:
    rid = run_async(ctx, lambda c, u: c.data.delete_education(u, id), writes=True)
    console.print(f"Deleted education {short(rid)}")


# --- languages -----------------------------------------------------------------


def _language_fields(language: str | None, level: str | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if language is not None:
        if not (code := language_code(language)):
            raise InputError(f"Unknown language: {language}")
        fields["language_code"] = code
    if level is not None:
        if not (cefr := cefr_level(level)):
            raise InputError(f"Unknown level: {level} (use A1–C2 or native)")
        fields["level"] = cefr
    return fields


@language_app.command("list")
def language_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    rows = run_async(ctx, lambda c, u: c.data.list_languages(u), writes=False)
    print_records("Languages", rows, ["id", "language_code", "level"], as_json=as_json)


@language_app.command("add")
def language_add(
    ctx: typer.Context,
    language: Annotated[str, Opt(help="Name or ISO 639-1 code.")],
    level: Annotated[str, Opt(help="A1–C2, native, or a fluency text.")],
) -> None:
    fields = _language_fields(language, level)
    lang = run_async(ctx, lambda c, u: c.data.create_language(u, fields), writes=True)
    created("language", lang.id)


@language_app.command("edit")
def language_edit(
    ctx: typer.Context,
    id: str,
    language: Annotated[str | None, Opt()] = None,
    level: Annotated[str | None, Opt()] = None,
) -> None:
    fields = _language_fields(language, level)
    lang = run_async(ctx, lambda c, u: c.data.update_language(u, id, fields), writes=True)
    console.print(f"Updated language {short(lang.id)}")


@language_app.command("rm")
def language_rm(ctx: typer.Context, id: str) -> None:
    rid = run_async(ctx, lambda c, u: c.data.delete_language(u, id), writes=True)
    console.print(f"Deleted language {short(rid)}")


# --- certificates ----------------------------------------------------------------


@certificate_app.command("list")
def certificate_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    rows = run_async(ctx, lambda c, u: c.data.list_certificates(u), writes=False)
    print_records(
        "Certificates", rows, ["id", "name", "issuer", "issued_month", "url"], as_json=as_json
    )


@certificate_app.command("add")
def certificate_add(
    ctx: typer.Context,
    name: Annotated[str, Opt()],
    issuer: Annotated[str, Opt()] = "",
    date: Annotated[str | None, Opt(help="YYYY-MM")] = None,
    url: Annotated[str, Opt()] = "",
) -> None:
    fields = {"name": name, "issuer": issuer, "issued_month": date, "url": url}
    cert = run_async(ctx, lambda c, u: c.data.create_certificate(u, fields), writes=True)
    created("certificate", cert.id)


@certificate_app.command("edit")
def certificate_edit(
    ctx: typer.Context,
    id: str,
    name: Annotated[str | None, Opt()] = None,
    issuer: Annotated[str | None, Opt()] = None,
    date: Annotated[str | None, Opt()] = None,
    url: Annotated[str | None, Opt()] = None,
) -> None:
    fields = given(name=name, issuer=issuer, issued_month=date, url=url)
    cert = run_async(ctx, lambda c, u: c.data.update_certificate(u, id, fields), writes=True)
    console.print(f"Updated certificate {short(cert.id)}")


@certificate_app.command("rm")
def certificate_rm(ctx: typer.Context, id: str) -> None:
    rid = run_async(ctx, lambda c, u: c.data.delete_certificate(u, id), writes=True)
    console.print(f"Deleted certificate {short(rid)}")


# --- awards ------------------------------------------------------------------------


@award_app.command("list")
def award_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    rows = run_async(ctx, lambda c, u: c.data.list_awards(u), writes=False)
    print_records(
        "Awards", rows, ["id", "title", "awarder", "awarded_month", "summary"], as_json=as_json
    )


@award_app.command("add")
def award_add(
    ctx: typer.Context,
    title: Annotated[str, Opt()],
    awarder: Annotated[str, Opt()] = "",
    date: Annotated[str | None, Opt(help="YYYY-MM")] = None,
    summary: Annotated[str, Opt()] = "",
) -> None:
    fields = {"title": title, "awarder": awarder, "awarded_month": date, "summary": summary}
    award = run_async(ctx, lambda c, u: c.data.create_award(u, fields), writes=True)
    created("award", award.id)


@award_app.command("edit")
def award_edit(
    ctx: typer.Context,
    id: str,
    title: Annotated[str | None, Opt()] = None,
    awarder: Annotated[str | None, Opt()] = None,
    date: Annotated[str | None, Opt()] = None,
    summary: Annotated[str | None, Opt()] = None,
) -> None:
    fields = given(title=title, awarder=awarder, awarded_month=date, summary=summary)
    award = run_async(ctx, lambda c, u: c.data.update_award(u, id, fields), writes=True)
    console.print(f"Updated award {short(award.id)}")


@award_app.command("rm")
def award_rm(ctx: typer.Context, id: str) -> None:
    rid = run_async(ctx, lambda c, u: c.data.delete_award(u, id), writes=True)
    console.print(f"Deleted award {short(rid)}")


# --- claims ------------------------------------------------------------------------


@claim_app.command("list")
def claim_list(
    ctx: typer.Context,
    type: Annotated[ClaimType | None, Opt()] = None,
    position: Annotated[str | None, Opt()] = None,
    provability: Annotated[Provability | None, Opt()] = None,
    status: Annotated[ClaimStatus | None, Opt()] = None,
    as_json: JsonOpt = False,
) -> None:
    rows = run_async(
        ctx,
        lambda c, u: c.data.list_claims(
            u, type=type, position=position, provability=provability, status=status
        ),
        writes=False,
    )
    if as_json:
        print_json(
            [
                {
                    **r.claim.model_dump(mode="json"),
                    "evidence": [e.model_dump(mode="json") for e in r.evidence],
                    "invalid_evidence": [e.model_dump(mode="json") for e in r.invalid_evidence],
                }
                for r in rows
            ]
        )
        return
    print_records(
        "Claims",
        [r.claim for r in rows],
        ["id", "type", "text", "position_id", "status", "provability", "keywords"],
        as_json=False,
    )


@claim_app.command("add")
def claim_add(
    ctx: typer.Context,
    type: Annotated[ClaimType, Opt()],
    text: Annotated[str, Opt()],
    position: Annotated[str | None, Opt()] = None,
    keywords: Annotated[str | None, Opt(help="Comma-separated.")] = None,
    lang: Annotated[str, Opt(help="ISO 639-1 code of the text.")] = "en",
    learnable: Annotated[bool, Opt(help="Not yet held, but learnable.")] = False,
) -> None:
    async def op(c: Container, u: UserContext) -> str:
        r = await c.data.create_claim(
            u,
            type=type,
            text=text,
            text_lang=lang,
            position=position,
            keywords=split_list(keywords),
            learnable=learnable,
        )
        return r.claim.id

    created("claim", run_async(ctx, op, writes=True))


@claim_app.command("edit")
def claim_edit(
    ctx: typer.Context,
    id: str,
    type: Annotated[ClaimType | None, Opt()] = None,
    text: Annotated[str | None, Opt()] = None,
    position: Annotated[str | None, Opt(help="Position id, or 'none' to untie.")] = None,
    keywords: Annotated[str | None, Opt(help="Comma-separated.")] = None,
    lang: Annotated[str | None, Opt()] = None,
) -> None:
    fields = given(type=type, text=text, keywords=split_list(keywords), text_lang=lang)
    if position is not None:
        fields["position_id"] = None if position.lower() == "none" else position
    cl = run_async(ctx, lambda c, u: c.data.update_claim(u, id, fields), writes=True)
    console.print(f"Updated claim {short(cl.id)}")


@claim_app.command("confirm")
def claim_confirm(ctx: typer.Context, id: str) -> None:
    claim = run_async(ctx, lambda c, u: c.data.confirm_claim(u, id), writes=True)
    console.print(f"Confirmed claim {short(claim.id)}")


@claim_app.command("rm")
def claim_rm(ctx: typer.Context, id: str) -> None:
    rid = run_async(ctx, lambda c, u: c.data.delete_claim(u, id), writes=True)
    console.print(f"Deleted claim {short(rid)}")


# --- evidence ------------------------------------------------------------------------


@evidence_app.command("add")
def evidence_add(
    ctx: typer.Context,
    claim: str,
    kind: Annotated[EvidenceKind, Opt()],
    document: Annotated[str | None, Opt()] = None,
    excerpt: Annotated[str, Opt()] = "",
    reference: Annotated[str, Opt(help="Reference person's name.")] = "",
    contact: Annotated[str, Opt(help="Reference person's contact.")] = "",
) -> None:
    r = run_async(
        ctx,
        lambda c, u: c.data.add_evidence(
            u,
            claim,
            kind=kind,
            document=document,
            excerpt=excerpt,
            reference_name=reference,
            reference_contact=contact,
        ),
        writes=True,
    )
    console.print(f"Claim {short(r.claim.id)} provability: {r.claim.provability}")


@evidence_app.command("reconfirm")
def evidence_reconfirm(
    ctx: typer.Context,
    id: str,
    excerpt: Annotated[str | None, Opt()] = None,
    fact_source: Annotated[
        bool, Opt("--fact-source", help="Reconfirm a fixed-record source.")
    ] = False,
) -> None:
    if fact_source:
        source = run_async(
            ctx, lambda c, u: c.data.reconfirm_fact_source(u, id, excerpt=excerpt), writes=True
        )
        console.print(f"Reconfirmed fact source {source.id}")
        return
    r = run_async(ctx, lambda c, u: c.data.reconfirm_evidence(u, id, excerpt=excerpt), writes=True)
    console.print(
        f"Reconfirmed evidence; claim {short(r.claim.id)} provability: {r.claim.provability}"
    )


@evidence_app.command("rm")
def evidence_rm(ctx: typer.Context, id: str) -> None:
    r = run_async(ctx, lambda c, u: c.data.remove_evidence(u, id), writes=True)
    console.print(
        f"Claim {short(r.claim.id)} provability: {r.claim.provability}, status: {r.claim.status}"
    )


# --- documents -------------------------------------------------------------------------


@document_app.command("list")
def document_list(ctx: typer.Context, as_json: JsonOpt = False) -> None:
    rows = run_async(ctx, lambda c, u: c.data.list_documents(u), writes=False)
    if as_json:
        print_json([d.model_dump(mode="json", exclude={"extracted_text"}) for d in rows])
        return
    print_records(
        "Documents",
        rows,
        ["id", "kind", "filename", "language", "issuer", "received_at"],
        as_json=False,
    )


@document_app.command("rm")
def document_rm(ctx: typer.Context, id: str) -> None:
    claims = run_async(ctx, lambda c, u: c.data.delete_document(u, id), writes=True)
    console.print(f"Deleted document; {len(claims)} claim(s) affected.")
    for cl in claims:
        console.print(f"  {short(cl.id)} → {cl.provability}, {cl.status}", highlight=False)
