"""Material run snapshots and structured statement provenance, without provider state."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel

from cvopt.domain.base import Model
from cvopt.domain.enums import Provability
from cvopt.domain.records import Award, Certificate, Claim, Education, Language, Position, Profile


class SourceProvenance(Model):
    id: str
    kind: str
    state: Literal["active", "stale", "invalid"]
    snapshot_origin: str
    document_id: str | None = None
    document_name: str = ""
    document_sha256: str = ""
    excerpt: str = ""
    reference_name: str = ""
    assertion: dict[str, Any] | None = None


class AssertionProvenance(Model):
    ref: str
    original_text: str
    eligible: bool
    provability: Provability
    status: str = "confirmed"
    sources: list[SourceProvenance] = []


class StatementProvenance(Model):
    element_id: str
    text: str
    source_claim_ids: list[str] = []
    source_record_refs: list[str] = []
    inferred_from: list[str] = []
    transformation_reason: str = ""
    sources: list[AssertionProvenance] = []


class ProfileSnapshot(Model):
    schema_version: int = 2
    fingerprint: str = ""
    profile: Profile
    claims: list[Claim]
    positions: list[Position]
    education: list[Education]
    languages: list[Language]
    certificates: list[Certificate]
    awards: list[Award]
    assertions: list[AssertionProvenance] = []


def snapshot_fingerprint(snapshot: ProfileSnapshot) -> str:
    """Material assertion/support identity; search hints and operation metadata excluded."""
    data = snapshot.model_dump(mode="json", exclude={"fingerprint"})
    for claim in data["claims"]:
        claim.pop("keywords", None)
    # Sorting removes database insertion/list ordering from semantic identity.
    for name in ("claims", "positions", "education", "languages", "certificates", "awards"):
        data[name] = sorted(data[name], key=lambda item: item["id"])
    data["assertions"] = sorted(data["assertions"], key=lambda item: item["ref"])
    for assertion in data["assertions"]:
        assertion["sources"] = sorted(assertion["sources"], key=lambda item: item["id"])
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def content_fingerprint(value: BaseModel) -> str:
    canonical = json.dumps(
        value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
