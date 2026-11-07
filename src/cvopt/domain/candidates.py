from cvopt.domain.base import Model
from cvopt.domain.enums import Fidelity, Provability, Slot


class RecordSource(Model):
    ref: str
    text: str
    provability: Provability


class Candidate(Model):
    id: str
    slot: Slot
    position_id: str | None
    text: str
    source_claim_ids: list[str]  # empty for invented or fixed-record statements
    source_record_refs: list[str] = []  # typed refs, currently position:<stored id>
    fidelity: Fidelity
    provability: Provability
    numeric_name_spans: list[str] = []  # invented only, exact spans approved by VERIFY_FACTS
    inferred_from: list[str] = []
    assertion_key: str | None = None  # invented only: the requirement id it covers
    lines: float


class Annotation(Model):
    pointer: str
    sentence: int | None = None
    text: str
    fidelity: Fidelity
    provability: Provability
    source_claim_ids: list[str]
    source_record_refs: list[str] = []
    inferred_from: list[str]
    requirement_ids: list[str]
    impact: float
    row: int  # report row number
