"""Read-only review projection and preflight; conflicts persist before records change."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from cvopt.domain.assertions import (
    assertion_fingerprint,
    check_documentary_assertion,
    check_evidence_support,
    check_fact_support,
    claim_assertion,
)
from cvopt.domain.enums import Decision, RecordType
from cvopt.domain.records import (
    FACT_MODELS,
    Award,
    Certificate,
    ClaimAssertion,
    Education,
    FactAssertion,
    Profile,
    SourceDocument,
)
from cvopt.domain.review import (
    AddEvidence,
    CreateAward,
    CreateCertificate,
    CreateClaim,
    CreateEducation,
    CreateLanguage,
    CreatePosition,
    LinkFactSource,
    Payload,
    ProposalItem,
    ReviewConflict,
    SetProfileField,
    UpdateAward,
    UpdateCertificate,
    UpdateEducation,
    UpdatePosition,
    created_assertion,
    review_target,
)
from cvopt.domain.text import normalize
from cvopt.errors import InputError, NotFoundError
from cvopt.ports import Repository

ITEM_REF = re.compile(r"^\$item:(\d+)$")


def item_ref(seq: int) -> str:
    return f"$item:{seq}"


def references(payload: Payload) -> set[int]:
    """Seqs of the batch items a payload refers to as `$item:<seq>`."""
    return {
        int(m.group(1))
        for key, value in payload.model_dump(mode="json").items()
        if key.endswith("_id") and isinstance(value, str) and (m := ITEM_REF.fullmatch(value))
    }


CREATE_TYPES = {
    RecordType.POSITION: CreatePosition,
    RecordType.EDUCATION: CreateEducation,
    RecordType.LANGUAGE: CreateLanguage,
    RecordType.CERTIFICATE: CreateCertificate,
    RecordType.AWARD: CreateAward,
}


def _named_identity(
    p: CreateCertificate | CreateAward | Certificate | Award,
) -> tuple[str, str, str]:
    if isinstance(p, CreateCertificate | Certificate):
        return normalize(p.name), normalize(p.issuer), p.issued_month or ""
    return normalize(p.title), normalize(p.awarder), p.awarded_month or ""


def _match_named_fact(
    p: CreateCertificate | CreateAward, records: Sequence[Certificate | Award]
) -> tuple[Certificate | Award | None, bool]:
    name, issuer, month = _named_identity(p)
    compatible = []
    for record in records:
        old_name, old_issuer, old_month = _named_identity(record)
        if (
            name == old_name
            and not (issuer and old_issuer and issuer != old_issuer)
            and not (month and old_month and month != old_month)
        ):
            compatible.append(record)
    if len(compatible) > 1 or (compatible and (not issuer or not month)):
        return None, True
    return (compatible[0], False) if compatible else (None, False)


def _named_fill(p: CreateCertificate | CreateAward, stored: Certificate | Award) -> dict[str, Any]:
    values = p.model_dump(exclude={"action", "excerpt", "name", "title"})
    return {key: value for key, value in values.items() if value and not getattr(stored, key)}


async def _fact(repo: Repository, record_type: RecordType, record_id: str) -> FactAssertion:
    if record_type is RecordType.POSITION:
        return await repo.get_position(record_id)
    if record_type is RecordType.EDUCATION:
        return await repo.get_education(record_id)
    if record_type is RecordType.LANGUAGE:
        return await repo.get_language(record_id)
    if record_type is RecordType.CERTIFICATE:
        return await repo.get_certificate(record_id)
    return await repo.get_award(record_id)


def _dependency_error(item: ProposalItem, by_seq: dict[int, ProposalItem]) -> str | None:
    p = item.payload
    for seq in references(p):
        parent = by_seq.get(seq)
        if parent is None or seq >= item.seq or parent.decision is not Decision.ACCEPT:
            return (
                f"Dependency #{seq} must be an accepted earlier record; edit or review its parent."
            )
        expected = (
            CreateClaim
            if isinstance(p, AddEvidence)
            else CREATE_TYPES[p.record_type]
            if isinstance(p, LinkFactSource)
            else CreateEducation
            if isinstance(p, UpdateEducation)
            else CreateCertificate
            if isinstance(p, UpdateCertificate)
            else CreateAward
            if isinstance(p, UpdateAward)
            else CreatePosition
        )
        if not isinstance(parent.payload, expected):
            return f"Dependency #{seq} does not create the required record type."
    return None


def _materially_edited(p: Payload, original: Payload | None) -> bool:
    if original is None or type(p) is not type(original):
        return True
    excluded = {"action", "excerpt", "evidence", "keywords"}
    return p.model_dump(exclude=excluded) != original.model_dump(exclude=excluded)


UPDATES = (UpdatePosition, UpdateEducation, UpdateCertificate, UpdateAward)


def _update_target(
    p: UpdatePosition | UpdateEducation | UpdateCertificate | UpdateAward,
) -> tuple[RecordType, str]:
    if isinstance(p, UpdatePosition):
        return RecordType.POSITION, p.position_id
    if isinstance(p, UpdateEducation):
        return RecordType.EDUCATION, p.education_id
    if isinstance(p, UpdateCertificate):
        return RecordType.CERTIFICATE, p.certificate_id
    return RecordType.AWARD, p.award_id


def _updated_fact(p: Payload, before: FactAssertion) -> FactAssertion:
    """The same fill-only changes drive preflight projection and application."""
    values = p.model_dump(
        exclude={"action", "excerpt", "position_id", "education_id", "certificate_id", "award_id"}
    )
    fill = {k: v for k, v in values.items() if v is not None and not getattr(before, k)}
    if isinstance(p, UpdateEducation):
        assert isinstance(before, Education)
        highlights = list(before.highlights)
        seen = {normalize(h) for h in highlights}
        for h in p.highlights:
            if normalize(h) not in seen:
                highlights.append(h)
                seen.add(normalize(h))
        links = {link.url: link.model_dump() for link in before.links}
        for link in p.links:
            links.setdefault(link.url, link.model_dump())
        fill.update(highlights=highlights, links=list(links.values()))
    return type(before).model_validate({**before.model_dump(), **fill})


@dataclass
class _Projection:
    repo: Repository
    profile: Profile
    facts: dict[tuple[RecordType, str], FactAssertion]
    claims: dict[str, ClaimAssertion]
    aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    async def load(cls, repo: Repository) -> "_Projection":
        facts: dict[tuple[RecordType, str], FactAssertion] = {}
        for record_type, records in (
            (RecordType.POSITION, await repo.list_positions()),
            (RecordType.EDUCATION, await repo.list_education()),
            (RecordType.LANGUAGE, await repo.list_languages()),
            (RecordType.CERTIFICATE, await repo.list_certificates()),
            (RecordType.AWARD, await repo.list_awards()),
        ):
            facts.update({(record_type, r.id): r for r in records})
        return cls(
            repo,
            await repo.get_profile(),
            facts,
            {c.id: claim_assertion(c) for c in await repo.list_claims()},
        )

    async def fact(self, record_type: RecordType, ref: str) -> FactAssertion:
        ref = self.aliases.get(ref, ref)
        if (record_type, ref) not in self.facts:
            record = await _fact(self.repo, record_type, ref)
            ref = record.id
        return self.facts[record_type, ref]

    async def target(self, p: Payload) -> ClaimAssertion | FactAssertion | None:
        reference = review_target(p)
        if reference:
            record_type, ref = reference
            if record_type != "claim":
                return await self.fact(record_type, ref)
            ref = self.aliases.get(ref, ref)
            if ref not in self.claims:
                ref = (await self.repo.get_claim(ref)).id
            return self.claims[ref]
        return None

    async def target_id(self, p: Payload) -> str | None:
        reference = review_target(p)
        if reference is None:
            return None
        record_type, ref = reference
        if ITEM_REF.fullmatch(ref):
            return ref
        if record_type == "claim":
            return (await self.repo.get_claim(ref)).id
        return (await self.fact(record_type, ref)).id

    def named_records(self, p: CreateCertificate | CreateAward) -> list[Certificate | Award]:
        record_type = (
            RecordType.CERTIFICATE if isinstance(p, CreateCertificate) else RecordType.AWARD
        )
        return [
            r
            for (kind, _), r in self.facts.items()
            if kind is record_type and isinstance(r, Certificate | Award)
        ]

    async def advance(self, item: ProposalItem) -> None:
        p = item.payload
        if isinstance(p, SetProfileField):
            self.profile = self.profile.model_copy(update={p.field: p.value})
        elif isinstance(p, UPDATES):
            record_type, ref = _update_target(p)
            before = await self.fact(record_type, ref)
            self.facts[record_type, before.id] = _updated_fact(p, before)
        elif snapshot := created_assertion(p, item_ref(item.seq)):
            if isinstance(snapshot, ClaimAssertion):
                self.claims[item_ref(item.seq)] = snapshot
            else:
                record_type = next(
                    kind for kind, model in FACT_MODELS.items() if isinstance(snapshot, model)
                )
                if isinstance(p, CreateCertificate | CreateAward):
                    match, _ = _match_named_fact(p, self.named_records(p))
                    if match:
                        self.aliases[item_ref(item.seq)] = match.id
                        snapshot = type(match).model_validate(
                            {**match.model_dump(), **_named_fill(p, match)}
                        )
                self.facts[record_type, snapshot.id] = snapshot


async def _check_item(
    repo: Repository,
    item: ProposalItem,
    by_seq: dict[int, ProposalItem],
    document: SourceDocument,
    projection: _Projection,
    *,
    renewed: bool = False,
) -> ReviewConflict | None:
    """Read-only validation before decisions or any domain write."""
    p, ctx = item.payload, item.review_context
    excerpt = (
        p.evidence.excerpt
        if isinstance(p, AddEvidence | CreateClaim)
        else getattr(p, "excerpt", "")
    )
    prior = ctx.target_assertion if ctx else None
    current: dict[str, Any] | None = None
    target_id: str | None = None

    def conflict(reason: str) -> ReviewConflict:
        return ReviewConflict(
            reason=reason,
            prior_assertion=prior,
            current_assertion=current,
            source_excerpt=excerpt,
            prior_target_id=ctx.target_id if ctx else None,
            current_target_id=target_id,
        )

    if error := _dependency_error(item, by_seq):
        return conflict(error)
    if ctx and ctx.conflict and not renewed:
        return ctx.conflict
    try:
        if review_target(p):
            target = await projection.target(p)
            assert target is not None
            target_id = await projection.target_id(p)
            current = target.model_dump(mode="json")
            if not renewed and (
                not ctx
                or ctx.target_assertion is None
                or ctx.target_id != target_id
                or ctx.target_fingerprint != assertion_fingerprint(target)
            ):
                return conflict(
                    "Target assertion changed or its prior identity is unknown; "
                    "review the current assertion and source again."
                )
        if isinstance(p, SetProfileField):
            stored = getattr(projection.profile, p.field)
            current = {p.field: stored}
            if stored and stored != p.value:
                return conflict(
                    "Profile field already has a different value; retain it or edit the proposal."
                )
        elif isinstance(p, AddEvidence | LinkFactSource):
            target = await projection.target(p)
            assert target is not None
            if isinstance(p, AddEvidence):
                assert isinstance(target, ClaimAssertion)
                check_documentary_assertion(target, p.evidence.kind, document, excerpt)
            if isinstance(p, AddEvidence):
                check_evidence_support(
                    p.evidence.kind, document, excerpt, p.evidence.reference_name
                )
            else:
                check_fact_support(document, excerpt)
        elif isinstance(p, CreateClaim):
            source = (
                ctx.original_payload
                if ctx
                and isinstance(ctx.original_payload, CreateClaim)
                and _materially_edited(p, ctx.original_payload)
                else p
            )
            check_evidence_support(
                source.evidence.kind,
                document,
                source.evidence.excerpt,
                source.evidence.reference_name,
            )
            assertion = ClaimAssertion(
                type=source.type,
                text=source.text,
                text_lang=source.text_lang,
                position_id=source.position_id,
            )
            check_documentary_assertion(
                assertion, source.evidence.kind, document, source.evidence.excerpt
            )
            if p.position_id and not ITEM_REF.fullmatch(p.position_id):
                await projection.fact(RecordType.POSITION, p.position_id)
        elif isinstance(
            p,
            tuple(CREATE_TYPES.values())
            + (UpdatePosition, UpdateEducation, UpdateCertificate, UpdateAward),
        ):
            source = (
                ctx.original_payload if ctx and _materially_edited(p, ctx.original_payload) else p
            )
            check_fact_support(document, getattr(source, "excerpt", ""))
            if isinstance(p, UPDATES):
                record_type, ref = _update_target(p)
                await projection.fact(record_type, ref)
            elif isinstance(p, CreateCertificate | CreateAward):
                records = projection.named_records(p)
                _, uncertain = _match_named_fact(p, records)
                if uncertain:
                    current = {"candidates": [r.model_dump(mode="json") for r in records]}
                    return conflict(
                        "Missing or ambiguous name, issuer and date identity; "
                        "review and edit the proposal."
                    )
    except (ValueError, InputError, NotFoundError) as err:
        return conflict(str(err))
    return None


async def _preflight(
    repo: Repository, items: list[ProposalItem], document: SourceDocument
) -> list[int]:
    by_seq = {i.seq: i for i in items}
    conflicts = []
    projection = await _Projection.load(repo)
    for item in items:
        if item.decision is Decision.ACCEPT:
            if conflict := await _check_item(repo, item, by_seq, document, projection):
                await repo.mark_item_conflict(item.id, conflict)
                by_seq[item.seq] = item.model_copy(update={"decision": Decision.PENDING})
                conflicts.append(item.seq)
            else:
                await projection.advance(item)
    return conflicts
