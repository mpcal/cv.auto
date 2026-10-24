"""IngestionService (F1): store a document, extract proposals, review and apply them."""

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from rapidfuzz import fuzz

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.enums import (
    BatchStatus,
    ClaimStatus,
    ClaimType,
    Decision,
    DocumentKind,
    EvidenceKind,
    Origin,
    RecordType,
)
from cvopt.domain.records import Claim, Position, Profile
from cvopt.domain.review import (
    AddEvidence,
    Conflict,
    CreateCertificate,
    CreateClaim,
    CreateEducation,
    CreateLanguage,
    CreatePosition,
    EvidenceDraft,
    LinkFactSource,
    Note,
    Payload,
    ProposalItem,
    ReviewBatch,
    SetProfileField,
    UpdatePosition,
)
from cvopt.domain.rules import claim_provability, order_positions
from cvopt.domain.text import normalize
from cvopt.errors import ConflictError, InputError
from cvopt.llm.runner import TaskRunner, TaskRunnerFactory
from cvopt.llm.tasks.common import ExCertificate, ExEducation, ExLanguage, ExPosition
from cvopt.llm.tasks.extract_certificate import EXTRACT_CERTIFICATE, CertificateInput
from cvopt.llm.tasks.extract_cv import EXTRACT_CV, CVInput, CVOutput
from cvopt.llm.tasks.extract_reference_letter import (
    EVIDENCE_GRADES,
    EXTRACT_REFERENCE_LETTER,
    Grade,
    LetterInput,
    LetterOutput,
    PositionRef,
)
from cvopt.llm.tasks.match_claims import MATCH_CLAIMS, ClaimRef, MatchInput
from cvopt.llm.tasks.transcribe_document import TRANSCRIBE_DOCUMENT, TranscribeInput
from cvopt.ports import (
    Clock,
    FileStore,
    LanguageDetector,
    Repository,
    RepositoryFactory,
    TextExtractor,
)
from cvopt.services.data import recompute_provability

ITEM_REF = re.compile(r"^\$item:(\d+)$")
PAYLOAD = TypeAdapter[Payload](Payload)
PROFILE_FIELDS = ("name", "email", "phone", "city", "country", "website")


@dataclass(frozen=True)
class IngestResult:
    document_id: str | None
    batch_id: str | None
    counts: dict[str, int]
    duplicate_of: str | None = None  # the already ingested document with the same content


@dataclass(frozen=True)
class DecideResult:
    item: ProposalItem
    rejected_dependents: list[ProposalItem]


@dataclass(frozen=True)
class ApplyResult:
    applied: int
    rejected: int


def item_ref(seq: int) -> str:
    return f"$item:{seq}"


def references(payload: Payload) -> set[int]:
    """Seqs of the batch items a payload refers to as `$item:<seq>`."""
    found = re.findall(r'"\$item:(\d+)"', json.dumps(payload.model_dump(mode="json")))
    return {int(n) for n in found}


def employer_matches(a: str, b: str, threshold: int) -> bool:
    return fuzz.token_set_ratio(normalize(a), normalize(b)) >= threshold


def overlaps(p: Position, start: str, end: str | None) -> bool:
    return p.start_month <= (end or "9999-12") and start <= (p.end_month or "9999-12")


def differs(a: str | None, b: str | None) -> bool:
    return normalize(a or "") != normalize(b or "")


@dataclass
class Proposals:
    """The items of one batch under construction, numbered from 1."""

    items: list[tuple[Payload, str | None]] = field(default_factory=list)

    def add(self, payload: Payload, target_id: str | None = None) -> int:
        self.items.append((payload, target_id))
        return len(self.items)


class _Builder:
    """Turns one document's extraction into proposal items, matched against stored data."""

    def __init__(
        self, repo: Repository, runner: TaskRunner, settings: Settings, language: str
    ) -> None:
        self._repo = repo
        self._runner = runner
        self._settings = settings
        self._language = language
        self.out = Proposals()
        self.issuer = ""
        self.position_id: str | None = None

    async def load(self) -> None:
        self.profile: Profile = await self._repo.get_profile()
        self.positions = await self._repo.list_positions()
        self.claims = await self._repo.list_claims()

    # --- fact records -------------------------------------------------------------

    def profile_fields(self, values: dict[str, str]) -> None:
        stored = self.profile.model_dump()
        for name in PROFILE_FIELDS:
            value = values.get(name, "").strip()
            if not value:
                continue
            if not stored[name]:
                self.out.add(SetProfileField(field=name, value=value))
            elif differs(stored[name], value):
                self.out.add(
                    Conflict(
                        record_type="profile",
                        record_id="profile",
                        field=name,
                        stored_value=stored[name],
                        document_value=value,
                    )
                )

    def position(self, p: ExPosition) -> str:
        """Matches or proposes a position; returns its id or `$item:<seq>`."""
        end = p.end_month or None
        threshold = self._settings.matching.employer_similarity
        match = next(
            (
                s
                for s in order_positions(self.positions)
                if employer_matches(s.employer, p.employer, threshold)
                and overlaps(s, p.start_month, end)
            ),
            None,
        )
        if match is None:
            return item_ref(
                self.out.add(
                    CreatePosition(
                        employer=p.employer,
                        employer_location=p.employer_location,
                        title=p.title,
                        start_month=p.start_month,
                        end_month=end,
                    )
                )
            )
        self.out.add(LinkFactSource(record_type=RecordType.POSITION, record_id=match.id), match.id)
        if p.employer_location and not match.employer_location:
            self.out.add(
                UpdatePosition(position_id=match.id, employer_location=p.employer_location),
                match.id,
            )
        for name, stored, new in [
            ("title", match.title, p.title),
            ("start_month", match.start_month, p.start_month),
            ("end_month", match.end_month or "current", end or "current"),
        ]:
            if differs(stored, new):
                self.out.add(
                    Conflict(
                        record_type="position",
                        record_id=match.id,
                        field=name,
                        stored_value=stored,
                        document_value=new,
                    ),
                    match.id,
                )
        return match.id

    async def education(self, e: ExEducation) -> None:
        threshold = self._settings.matching.employer_similarity
        for s in await self._repo.list_education():
            if s.degree_level == e.degree_level and employer_matches(
                s.institution, e.institution, threshold
            ):
                self.out.add(LinkFactSource(record_type=RecordType.EDUCATION, record_id=s.id), s.id)
                return
        self.out.add(
            CreateEducation(
                institution=e.institution,
                area=e.area,
                degree_type=e.degree_type,
                degree_level=e.degree_level,
                start_month=e.start_month or None,
                end_month=e.end_month or None,
                grade=e.grade,
            )
        )

    async def language_record(self, lang: ExLanguage) -> None:
        for s in await self._repo.list_languages():
            if s.language_code == lang.language_code:
                if s.level != lang.level:
                    self.out.add(
                        Conflict(
                            record_type="language",
                            record_id=s.id,
                            field="level",
                            stored_value=s.level,
                            document_value=lang.level,
                        ),
                        s.id,
                    )
                else:
                    self.out.add(
                        LinkFactSource(record_type=RecordType.LANGUAGE, record_id=s.id), s.id
                    )
                return
        self.out.add(CreateLanguage(language_code=lang.language_code, level=lang.level))

    async def certificate(self, c: ExCertificate) -> None:
        for s in await self._repo.list_certificates():
            if normalize(s.name) == normalize(c.name):
                self.out.add(
                    LinkFactSource(record_type=RecordType.CERTIFICATE, record_id=s.id), s.id
                )
                return
        self.out.add(
            CreateCertificate(
                name=c.name, issuer=c.issuer, issued_month=c.issued_month or None, url=c.url
            )
        )

    # --- claims ---------------------------------------------------------------------

    async def claims_for(
        self, new: Sequence[tuple[ClaimType, str, list[str], str | None, EvidenceDraft]]
    ) -> None:
        """Each new claim becomes evidence for a matching stored claim, a conflict,
        or a new claim. Matching runs concurrently; items keep the input order."""
        decisions = await asyncio.gather(
            *(self._match(text, kw, pos) for _, text, kw, pos, _ in new)
        )
        for (type_, text, keywords, position_id, evidence), (match, conflict) in zip(
            new, decisions, strict=True
        ):
            if match and conflict:
                self.out.add(
                    Conflict(
                        record_type="claim",
                        record_id=match.id,
                        field="text",
                        stored_value=match.text,
                        document_value=text,
                    ),
                    match.id,
                )
            elif match:
                self.out.add(AddEvidence(claim_id=match.id, evidence=evidence), match.id)
            else:
                self.out.add(
                    CreateClaim(
                        type=type_,
                        text=text,
                        text_lang=self._language,
                        position_id=position_id,
                        keywords=keywords,
                        evidence=evidence,
                    )
                )

    async def _match(
        self, text: str, keywords: list[str], position_id: str | None
    ) -> tuple[Claim | None, bool]:
        if position_id is not None and ITEM_REF.match(position_id):
            return None, False  # a position proposed in this batch has no stored claims
        wanted = {normalize(k) for k in keywords}
        candidates = sorted(
            (
                c
                for c in self.claims
                if c.position_id == position_id and wanted & {normalize(k) for k in c.keywords}
            ),
            key=lambda c: (normalize(c.text), c.id),
        )
        if not candidates:
            return None, False
        refs = {f"C{i}": c for i, c in enumerate(candidates, start=1)}
        out = await self._runner.run(
            MATCH_CLAIMS,
            MatchInput(
                new=ClaimRef(ref="NEW", text=text, keywords=keywords),
                existing=[
                    ClaimRef(ref=r, text=c.text, keywords=c.keywords) for r, c in refs.items()
                ],
            ),
        )
        same = sorted((v for v in out.verdicts if v.same), key=lambda v: v.ref)
        return (refs[same[0].ref], same[0].quantity_conflict) if same else (None, False)

    # --- per document kind ------------------------------------------------------------

    async def cv(self, text: str) -> None:
        out: CVOutput = await self._runner.run(
            EXTRACT_CV, CVInput(text=text, language=self._language)
        )
        self.profile_fields(out.profile.model_dump())
        position_ids = [self.position(p) for p in out.positions]
        for e in out.education:
            await self.education(e)
        for lang in out.languages:
            await self.language_record(lang)
        for c in out.certificates:
            await self.certificate(c)
        evidence = EvidenceDraft(kind=EvidenceKind.OWN_CV)
        await self.claims_for(
            [
                (
                    c.type,
                    c.text,
                    c.keywords,
                    position_ids[c.position] if c.position >= 0 else None,
                    evidence,
                )
                for c in out.claims
            ]
        )

    async def reference_letter(self, text: str) -> None:
        ordered = order_positions(self.positions)
        refs = {f"P{i}": p for i, p in enumerate(ordered, start=1)}
        out: LetterOutput = await self._runner.run(
            EXTRACT_REFERENCE_LETTER,
            LetterInput(
                text=text,
                language=self._language,
                positions=[
                    PositionRef(
                        ref=r,
                        employer=p.employer,
                        title=p.title,
                        start_month=p.start_month,
                        end_month=p.end_month or "",
                    )
                    for r, p in refs.items()
                ],
            ),
        )
        self.issuer = out.issuer
        if out.position_ref:
            position_id = refs[out.position_ref].id
            self.position_id = position_id
            self.out.add(
                LinkFactSource(record_type=RecordType.POSITION, record_id=position_id), position_id
            )
        else:
            position_id = self.position(out.new_position)
        new = []
        for p in out.passages:
            if p.grade in EVIDENCE_GRADES:
                evidence = EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt=p.excerpt)
                new += [(c.type, c.text, c.keywords, position_id, evidence) for c in p.claims]
            elif p.grade in (Grade.SATISFACTORY, Grade.POOR):
                self.out.add(
                    Note(
                        text=f"Passage graded {p.grade.value}, not used as evidence: “{p.excerpt}”"
                    )
                )
        await self.claims_for(new)

    async def certificate_document(self, text: str, kind: DocumentKind) -> None:
        out = await self._runner.run(
            EXTRACT_CERTIFICATE,
            CertificateInput(text=text, language=self._language, kind=kind.value),
        )
        if out.record_type == "education":
            await self.education(out.education)
            self.issuer = out.education.institution
        else:
            await self.certificate(out.certificate)
            self.issuer = out.certificate.issuer
        evidence = EvidenceDraft(kind=EvidenceKind.CERTIFICATE_OR_DIPLOMA)
        await self.claims_for(
            [(ClaimType.SKILL, c.text, c.keywords, None, evidence) for c in out.claims]
        )


class IngestionService:
    def __init__(
        self,
        repos: RepositoryFactory,
        runners: TaskRunnerFactory,
        files: FileStore,
        extractor: TextExtractor,
        detector: LanguageDetector,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._repos = repos
        self._runners = runners
        self._files = files
        self._extractor = extractor
        self._detector = detector
        self._clock = clock
        self._settings = settings

    # --- ingestion ----------------------------------------------------------------------

    async def ingest_document(
        self, user: UserContext, path: Path, kind: DocumentKind
    ) -> IngestResult:
        repo = self._repos.for_user(user)
        extracted = await self._extractor.extract(path)  # validates type and size first
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if (known := await repo.find_document_by_sha256(sha)) is not None:
            return IngestResult(known.id, None, {}, duplicate_of=known.id)
        runner = self._runners.for_user(user)
        text = extracted.text
        if extracted.needs_transcription:
            text = (
                await runner.run(
                    TRANSCRIBE_DOCUMENT, TranscribeInput(media_type=extracted.media_type, data=data)
                )
            ).text
        if not text.strip():
            raise InputError(f"No text found in {path.name}.")
        language = self._detector.detect(text)

        builder = _Builder(repo, runner, self._settings, language)
        await builder.load()
        if kind is DocumentKind.CV:
            await builder.cv(text)
        elif kind is DocumentKind.REFERENCE_LETTER:
            await builder.reference_letter(text)
        elif kind in (DocumentKind.DIPLOMA, DocumentKind.CERTIFICATE):
            await builder.certificate_document(text, kind)

        async with repo.transaction():
            doc = await repo.insert_document(
                {
                    "kind": kind,
                    "filename": path.name,
                    "sha256": sha,
                    "mime_type": extracted.media_type,
                    "language": language,
                    "issuer": builder.issuer,
                    "position_id": builder.position_id,
                    "extracted_text": text,
                    "received_at": self._clock.now(),
                }
            )
            batch_id = None
            if builder.out.items:
                batch = await repo.insert_batch(doc.id)
                batch_id = batch.id
                for seq, (payload, target) in enumerate(builder.out.items, start=1):
                    await repo.insert_item(batch.id, seq, payload, target)
            self._files.save_document(user, doc.id, path.suffix, data)
        counts = Counter(p.action.value for p, _ in builder.out.items)
        return IngestResult(doc.id, batch_id, dict(sorted(counts.items())))

    # --- review -------------------------------------------------------------------------

    async def list_batches(
        self, user: UserContext, status: BatchStatus | None = None
    ) -> list[ReviewBatch]:
        return await self._repos.for_user(user).list_batches(status)

    async def get_batch(self, user: UserContext, ref: str) -> ReviewBatch:
        return await self._repos.for_user(user).get_batch(ref)

    async def list_items(self, user: UserContext, batch_ref: str) -> list[ProposalItem]:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            batch = await repo.get_batch(batch_ref)
            return await repo.list_items(batch.id)

    async def _open_batch(self, repo: Repository, ref: str) -> ReviewBatch:
        batch = await repo.get_batch(ref)
        if batch.status is not BatchStatus.OPEN:
            raise ConflictError(f"Batch {batch.id} is {batch.status.value}.")
        return batch

    async def decide_item(
        self,
        user: UserContext,
        batch_ref: str,
        seq: int,
        decision: Decision,
        payload: Payload | dict[str, Any] | None = None,
    ) -> DecideResult:
        """Saves one decision. An edited payload is validated and accepted; rejecting an
        item also rejects the items that depend on it."""
        repo = self._repos.for_user(user)
        async with repo.transaction():
            batch = await self._open_batch(repo, batch_ref)
            items = await repo.list_items(batch.id)
            by_seq = {i.seq: i for i in items}
            if seq not in by_seq:
                raise InputError(f"Batch {batch.id} has no item {seq}.")
            item = by_seq[seq]
            edited = None
            if payload is not None:
                edited = _validate_edit(item, payload, by_seq)
                decision = Decision.ACCEPT
            await repo.update_item(item.id, decision, edited)
            rejected: list[ProposalItem] = []
            if decision is Decision.REJECT:
                for dep in _dependents(items, seq):
                    if dep.decision is not Decision.REJECT:
                        await repo.update_item(
                            dep.id, Decision.REJECT, note=f"rejected with item {seq}"
                        )
                        rejected.append(dep)
            updated = {i.seq: i for i in await repo.list_items(batch.id)}
        return DecideResult(updated[seq], [updated[d.seq] for d in rejected])

    async def accept_all(self, user: UserContext, batch_ref: str) -> int:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            batch = await self._open_batch(repo, batch_ref)
            pending = [i for i in await repo.list_items(batch.id) if i.decision is Decision.PENDING]
            for i in pending:
                await repo.update_item(i.id, Decision.ACCEPT)
        return len(pending)

    async def discard_batch(self, user: UserContext, batch_ref: str) -> ReviewBatch:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            batch = await self._open_batch(repo, batch_ref)
            return await repo.update_batch(batch.id, {"status": BatchStatus.DISCARDED})

    async def apply_batch(self, user: UserContext, batch_ref: str) -> ApplyResult:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            batch = await self._open_batch(repo, batch_ref)
            items = await repo.list_items(batch.id)
            if pending := [i.seq for i in items if i.decision is Decision.PENDING]:
                raise ConflictError(
                    f"{len(pending)} item(s) still pending: {', '.join(map(str, pending))}."
                )
            created: dict[int, str] = {}
            claim_ids: set[str] = set()
            applied = 0
            for item in items:
                if item.decision is not Decision.ACCEPT:
                    continue
                payload = PAYLOAD.validate_python(item.payload.model_dump())  # re-validate
                if not references(payload) <= created.keys():
                    continue  # depends on an item that was rejected or not applied
                payload = _resolve(payload, created)
                new_id = await _apply(repo, payload, batch.document_id, claim_ids)
                if new_id:
                    created[item.seq] = new_id
                applied += 1
            await recompute_provability(repo, sorted(claim_ids))
            await repo.update_batch(
                batch.id, {"status": BatchStatus.APPLIED, "applied_at": self._clock.now()}
            )
        return ApplyResult(applied=applied, rejected=len(items) - applied)


def _dependents(items: Iterable[ProposalItem], seq: int) -> list[ProposalItem]:
    """Items that refer to `seq`, directly or through other dependents."""
    out: list[ProposalItem] = []
    frontier = {seq}
    remaining = [i for i in items if i.seq != seq]
    while frontier:
        found = [i for i in remaining if references(i.payload) & frontier]
        remaining = [i for i in remaining if i not in found]
        out += found
        frontier = {i.seq for i in found}
    return sorted(out, key=lambda i: i.seq)


def _validate_edit(
    item: ProposalItem, payload: Payload | dict[str, Any], by_seq: dict[int, ProposalItem]
) -> Payload:
    data = payload.model_dump(mode="json") if not isinstance(payload, dict) else payload
    try:
        edited = PAYLOAD.validate_python({**data, "action": item.action.value})
    except ValidationError as err:
        raise InputError(f"Invalid edit of item {item.seq}: {err}") from err
    for ref in references(edited):
        if ref not in by_seq or ref >= item.seq:
            raise InputError(f"Item {item.seq} may only refer to earlier items, not {ref}.")
    return edited


def _resolve(payload: Payload, created: dict[int, str]) -> Payload:
    """Replaces `$item:<seq>` references with the ids those items created."""
    data = payload.model_dump(mode="json")
    for key, value in data.items():
        if isinstance(value, str) and (m := ITEM_REF.match(value)):
            data[key] = created[int(m.group(1))]
    return PAYLOAD.validate_python(data)


async def _apply(repo: Repository, p: Payload, document_id: str, claim_ids: set[str]) -> str | None:
    """Applies one accepted payload; returns the id of a record it created."""
    if isinstance(p, SetProfileField):
        profile = await repo.get_profile()
        await repo.save_profile(profile.model_copy(update={p.field: p.value}))
    elif isinstance(p, CreatePosition):
        pos = await repo.insert_position(p.model_dump(exclude={"action"}))
        await repo.insert_fact_source(RecordType.POSITION, pos.id, document_id)
        return pos.id
    elif isinstance(p, UpdatePosition):
        pos = await repo.get_position(p.position_id)
        stored = pos.model_dump()
        fill = {
            k: v
            for k, v in p.model_dump(exclude={"action", "position_id"}).items()
            if v is not None and not stored[k]
        }
        if fill:
            await repo.update_position(pos.id, fill)
    elif isinstance(p, CreateEducation | CreateLanguage | CreateCertificate):
        values = p.model_dump(exclude={"action"})
        if isinstance(p, CreateEducation):
            rec_type, rec_id = RecordType.EDUCATION, (await repo.insert_education(values)).id
        elif isinstance(p, CreateLanguage):
            rec_type, rec_id = RecordType.LANGUAGE, (await repo.insert_language(values)).id
        else:
            rec_type, rec_id = RecordType.CERTIFICATE, (await repo.insert_certificate(values)).id
        await repo.insert_fact_source(rec_type, rec_id, document_id)
        return rec_id
    elif isinstance(p, LinkFactSource):
        await repo.insert_fact_source(p.record_type, p.record_id, document_id)
    elif isinstance(p, CreateClaim):
        position_id = (await repo.get_position(p.position_id)).id if p.position_id else None
        claim = await repo.insert_claim(
            {
                "type": p.type,
                "text": p.text,
                "text_lang": p.text_lang,
                "position_id": position_id,
                "keywords": p.keywords,
                "status": ClaimStatus.CONFIRMED,
                "origin": Origin.EXTRACTED,
                "provability": claim_provability([p.evidence.kind]),
            }
        )
        await repo.insert_evidence(
            {"claim_id": claim.id, "document_id": document_id, **p.evidence.model_dump()}
        )
        claim_ids.add(claim.id)
        return claim.id
    elif isinstance(p, AddEvidence):
        claim = await repo.get_claim(p.claim_id)
        await repo.insert_evidence(
            {"claim_id": claim.id, "document_id": document_id, **p.evidence.model_dump()}
        )
        claim_ids.add(claim.id)
    # conflict and note items change nothing: accepting them means acknowledged
    return None
