"""Reusable original-profile loading, verified translation and neutral coverage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.cv import CVElement, JSONResume, Placement, degree_classifications, parse_elements
from cvopt.domain.enums import ClaimStatus, ClaimType, ElementKind, Provability, RecordType
from cvopt.domain.jd import JobDescription
from cvopt.domain.provenance import (
    AssertionProvenance,
    ProfileSnapshot,
    SourceProvenance,
    snapshot_fingerprint,
)
from cvopt.domain.records import Award, Certificate, Claim, Education, Language, Position, Profile
from cvopt.domain.rules import fact_record_provability, language_display, order_positions
from cvopt.domain.scoring import ScoreResult, score
from cvopt.errors import InputError, LLMOutputError, ValidationFailed
from cvopt.llm.judge import Judge
from cvopt.llm.runner import TaskRunner, TaskRunnerFactory
from cvopt.llm.tasks.translate import TRANSLATE, TextRef, TranslateInput
from cvopt.llm.tasks.verify_facts import VERIFY_FACTS, FactItem, FactsInput
from cvopt.ports import RepositoryFactory
from cvopt.services.scoring import scoring_params

TRANSLATE_BATCH = 20


@dataclass
class Facts:
    """The user's confirmed data, as generation sees it."""

    profile: Profile
    positions: list[Position]  # ordered as in P1 §6.2
    education: list[Education]
    languages: list[Language]
    certificates: list[Certificate]
    awards: list[Award]
    claims: dict[str, Claim]  # confirmed claims, text in the JD's language
    original_text: dict[str, str]  # claim id -> stored text
    original_claims: Mapping[str, Claim]  # immutable authoritative originals
    fact_provability: dict[tuple[RecordType, str], Provability]
    snapshot: ProfileSnapshot | None = None
    user_key: str | None = None


async def translate_claims(facts: Facts, lang: str, runner: TaskRunner, settings: Settings) -> None:
    facts.claims = {cid: c.model_copy(deep=True) for cid, c in facts.original_claims.items()}
    foreign = sorted((c for c in facts.claims.values() if c.text_lang != lang), key=lambda c: c.id)
    for start in range(0, len(foreign), TRANSLATE_BATCH):
        chunk = foreign[start : start + TRANSLATE_BATCH]
        refs = {f"T{i}": c for i, c in enumerate(chunk, start=1)}
        feedback = ""
        for attempt in range(1 + settings.llm.schema_retries):
            inp = TranslateInput(
                texts=[TextRef(ref=r, text=c.text) for r, c in refs.items()],
                target_language=lang,
                feedback=feedback,
            )
            try:
                out = await runner.run(TRANSLATE, inp)
                checked = await runner.run(
                    VERIFY_FACTS,
                    FactsInput(
                        items=[
                            FactItem(
                                ref=t.ref,
                                text=t.text,
                                sources=[refs[t.ref].text],
                                mode="translation",
                            )
                            for t in out.translations
                        ]
                    ),
                )
            except LLMOutputError as err:
                raise ValidationFailed("Translation could not preserve original facts.") from err
            rejected = [v for v in checked.verdicts if not v.facts_preserved]
            if not rejected:
                for t in out.translations:
                    c = refs[t.ref]
                    facts.claims[c.id] = c.model_copy(update={"text": t.text, "text_lang": lang})
                break
            await runner.invalidate(TRANSLATE, inp)
            feedback = f"Attempt {attempt + 1}: " + "; ".join(
                f"{v.ref}: {v.reason}" for v in rejected
            )
        else:
            raise ValidationFailed("Translation could not preserve original facts after retries.")


def fact_sections(facts: Facts, lang: str) -> dict[str, Any]:
    """Education, languages, certificates and awards exactly as stored."""
    education = []
    for e in facts.education:
        entry: dict[str, Any] = {"institution": e.institution}
        for key, value in (
            ("area", e.area),
            ("studyType", e.degree_type),
            ("startDate", e.start_month),
            ("endDate", e.end_month),
            ("score", e.grade),
            ("summary", e.summary),
            ("highlights", e.highlights),
            ("links", [link.model_dump() for link in e.links]),
        ):
            if value:
                entry[key] = value
        education.append(entry)
    languages = []
    for lg in facts.languages:
        name, fluency = language_display(lg.language_code, lg.level, lang)
        languages.append({"language": name, "fluency": fluency})
    certificates = []
    for c in facts.certificates:
        entry = {"name": c.name}
        for key, value in (("date", c.issued_month), ("issuer", c.issuer), ("url", c.url)):
            if value:
                entry[key] = value
        certificates.append(entry)
    awards = []
    for a in facts.awards:
        entry = {"title": a.title}
        for key, value in (
            ("date", a.awarded_month),
            ("awarder", a.awarder),
            ("summary", a.summary),
        ):
            if value:
                entry[key] = value
        awards.append(entry)
    return {
        "education": education,
        "languages": languages,
        "certificates": certificates,
        "awards": awards,
        "meta": degree_classifications(education, [e.degree_level for e in facts.education]),
    }


class ProfileCoverageService:
    def __init__(
        self, repos: RepositoryFactory, runners: TaskRunnerFactory, settings: Settings
    ) -> None:
        self._repos, self._runners, self._settings = repos, runners, settings

    async def load(self, user: UserContext) -> Facts:
        repo = self._repos.for_user(user)
        all_claims = await repo.list_claims()
        claims = [c for c in all_claims if c.status is ClaimStatus.CONFIRMED]
        documents = {d.id: d for d in await repo.list_documents()}
        docs = {d.id: d.kind for d in documents.values()}
        fact_prov: dict[tuple[RecordType, str], Provability] = {}
        education = await repo.list_education()
        languages = await repo.list_languages()
        certificates = await repo.list_certificates()
        facts = Facts(
            profile=await repo.get_profile(),
            positions=order_positions(await repo.list_positions()),
            education=education,
            languages=languages,
            certificates=certificates,
            awards=await repo.list_awards(),
            claims={c.id: c for c in claims},
            original_text={c.id: c.text for c in claims},
            original_claims=MappingProxyType({c.id: c.model_copy(deep=True) for c in claims}),
            fact_provability=fact_prov,
            user_key=user.user_key,
        )
        evidence = await repo.list_evidence()
        active_evidence = {e.id for e in await repo.list_evidence(active_only=True)}
        sources = await repo.list_fact_sources()
        active_sources = {s.id for s in await repo.list_fact_sources(active_only=True)}

        def source(link: Any, active: set[str]) -> SourceProvenance:
            doc = documents.get(link.document_id)
            return SourceProvenance(
                id=link.id,
                kind=link.kind.value if hasattr(link, "kind") else "fact_source",
                state="active" if link.id in active else "stale",
                snapshot_origin=link.snapshot_origin,
                document_id=link.document_id,
                document_name=doc.filename if doc else "",
                document_sha256=doc.sha256 if doc else "",
                excerpt=link.excerpt,
                reference_name=getattr(link, "reference_name", ""),
                assertion=link.assertion.model_dump(mode="json") if link.assertion else None,
            )

        assertions = [
            AssertionProvenance(
                ref=f"claim:{c.id}",
                original_text=c.text,
                status=c.status.value,
                eligible=c.status is ClaimStatus.CONFIRMED
                and c.provability in (Provability.MEDIUM, Provability.HIGH),
                provability=c.provability,
                sources=[source(e, active_evidence) for e in evidence if e.claim_id == c.id],
            )
            for c in all_claims
        ]
        for rtype, name in (
            (RecordType.POSITION, "positions"),
            (RecordType.EDUCATION, "education"),
            (RecordType.LANGUAGE, "languages"),
            (RecordType.CERTIFICATE, "certificates"),
            (RecordType.AWARD, "awards"),
        ):
            for record in getattr(facts, name):
                links = [
                    link
                    for link in sources
                    if link.record_type is rtype and link.record_id == record.id
                ]
                prov = fact_record_provability(
                    [
                        docs[link.document_id]
                        for link in links
                        if link.id in active_sources and link.document_id in docs
                    ]
                )
                fact_prov[rtype, record.id] = prov
                text = " · ".join(
                    str(v) for k, v in record.model_dump(mode="json").items() if k != "id" and v
                )
                assertions.append(
                    AssertionProvenance(
                        ref=f"{rtype.value}:{record.id}",
                        original_text=text,
                        eligible=True,
                        provability=prov,
                        sources=[source(link, active_sources) for link in links],
                    )
                )
        snapshot = ProfileSnapshot(
            profile=facts.profile,
            claims=all_claims,
            positions=facts.positions,
            education=education,
            languages=languages,
            certificates=certificates,
            awards=facts.awards,
            assertions=assertions,
        )
        facts.snapshot = snapshot.model_copy(update={"fingerprint": snapshot_fingerprint(snapshot)})
        return facts

    async def coverage(
        self,
        user: UserContext,
        jd: JobDescription,
        as_of: date,
        *,
        facts: Facts | None = None,
        translated: bool = False,
    ) -> ProfileCoverage:
        """Complete confirmed held assertions; no layout selection or placement discount.

        Pass the already loaded Facts to share one consistent snapshot with generation.
        Only that call may set translated=True, after translate_claims succeeded.
        Explanatory context remains in original wording and cannot contribute points.
        """
        facts = facts or await self.load(user)
        if facts.user_key is not None and facts.user_key != user.user_key:
            raise InputError("Profile facts belong to another user.")
        if not translated:
            facts = replace(facts, claims=dict(facts.claims))
            await translate_claims(facts, jd.language, self._runners.for_user(user), self._settings)
        params = scoring_params(self._settings)
        judge = Judge(
            self._runners.for_user(user), params, self._settings.matching.judge_batch_size
        )
        elements = profile_elements(facts, as_of, jd.language)
        values = await judge.values(jd.requirements, elements)
        result = score(
            elements, jd.requirements, lambda r, e: values.get((r, e), 0.0), params, as_of
        )
        contexts = context_elements(facts, as_of, jd.language)
        context_values = await judge.values(jd.requirements, contexts)
        assert facts.snapshot is not None
        return ProfileCoverage(
            result,
            elements,
            values,
            contexts,
            context_values,
            facts.snapshot,
            as_of,
            self._settings.model_dump(mode="json"),
        )


def profile_elements(facts: Facts, as_of: date, lang: str) -> list[CVElement]:
    """Full skill elements and optionally dated experience/achievement highlights."""
    positions = {p.id: p for p in facts.positions}
    out = []
    for c in facts.claims.values():
        if c.status is not ClaimStatus.CONFIRMED or c.provability not in (
            Provability.MEDIUM,
            Provability.HIGH,
        ):
            continue
        pos = positions.get(c.position_id or "") if c.type is not ClaimType.SKILL else None
        out.append(
            CVElement(
                id=f"claim:{c.id}",
                kind=ElementKind.SKILL if c.type is ClaimType.SKILL else ElementKind.HIGHLIGHT,
                text=c.text,
                placement=1,
                position_ref=pos.id if pos else None,
                start_month=pos.start_month if pos else None,
                end_month=pos.end_month if pos else None,
            )
        )
    out.extend(fixed_elements(facts, as_of, lang))
    return out


def fixed_elements(facts: Facts, as_of: date, lang: str) -> list[CVElement]:
    elements = [
        e.model_copy(update={"placement": 1.0})
        for e in parse_elements(
            JSONResume.model_validate(fact_sections(facts, lang)), as_of, lang, Placement()
        )
        if e.kind in (ElementKind.EDUCATION, ElementKind.LANGUAGE, ElementKind.CERTIFICATE)
    ]
    elements.extend(
        CVElement(
            id=f"position:{p.id}",
            kind=ElementKind.HIGHLIGHT,
            text=" · ".join(v for v in (p.title, p.employer) if v),
            placement=1,
            position_ref=p.id,
            start_month=p.start_month,
            end_month=p.end_month,
        )
        for p in facts.positions
    )
    elements.extend(
        CVElement(
            id=f"award:{a.id}",
            kind=ElementKind.HIGHLIGHT,
            text=" · ".join(v for v in (a.title, a.awarder, a.summary) if v),
            placement=1,
        )
        for a in facts.awards
    )
    return elements


def element_ref(facts: Facts, element: CVElement) -> str:
    if element.id.startswith(("claim:", "position:", "award:")):
        return element.id
    section, index = element.id.strip("/").split("/")[:2]
    rtype = {"education": "education", "languages": "language", "certificates": "certificate"}[
        section
    ]
    return f"{rtype}:{getattr(facts, section)[int(index)].id}"


def context_elements(facts: Facts, as_of: date, lang: str) -> list[CVElement]:
    if facts.snapshot is None:
        return []
    out = []
    for a in facts.snapshot.assertions:
        if a.ref.startswith("claim:") and not a.eligible:
            c = next(c for c in facts.snapshot.claims if f"claim:{c.id}" == a.ref)
            out.append(
                CVElement(
                    id=f"context:{a.ref}",
                    kind=ElementKind.SKILL if c.type is ClaimType.SKILL else ElementKind.HIGHLIGHT,
                    text=a.original_text,
                    placement=1,
                )
            )
        for source in a.sources:
            if source.state != "stale" or not source.assertion:
                continue
            assertion = source.assertion
            if "text" not in assertion:
                sections: dict[str, tuple[str, type[BaseModel]]] = {
                    "education": ("education", Education),
                    "language": ("languages", Language),
                    "certificate": ("certificates", Certificate),
                    "position": ("positions", Position),
                    "award": ("awards", Award),
                }
                kind = a.ref.split(":", 1)[0]
                if kind in sections:
                    name, model = sections[kind]
                    old_facts = replace(
                        facts, positions=[], awards=[], education=[], languages=[], certificates=[]
                    )
                    setattr(old_facts, name, [model.model_validate(assertion)])
                    out.extend(
                        e.model_copy(update={"id": f"stale:{source.id}"})
                        for e in fixed_elements(old_facts, as_of, lang)
                    )
                continue
            out.append(
                CVElement(
                    id=f"stale:{source.id}",
                    kind=ElementKind.SKILL
                    if assertion.get("type") == "skill"
                    else ElementKind.HIGHLIGHT,
                    text=assertion["text"],
                    placement=1,
                )
            )
    return out


@dataclass(frozen=True)
class ProfileCoverage:
    result: ScoreResult
    elements: list[CVElement]
    values: dict[tuple[str, str], float]
    context_elements: list[CVElement]
    context_values: dict[tuple[str, str], float]
    snapshot: ProfileSnapshot
    as_of: date
    config: dict[str, Any]
