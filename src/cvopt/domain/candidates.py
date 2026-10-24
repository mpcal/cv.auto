from cvopt.domain.base import Model
from cvopt.domain.enums import Fidelity, Provability, Slot


class Candidate(Model):
    id: str
    slot: Slot
    position_id: str | None
    text: str
    source_claim_ids: list[str]  # empty only for invented candidates
    fidelity: Fidelity
    provability: Provability
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
    inferred_from: list[str]
    requirement_ids: list[str]
    impact: float
    row: int  # report row number
