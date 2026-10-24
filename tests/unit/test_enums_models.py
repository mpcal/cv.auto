import pytest
from pydantic import TypeAdapter, ValidationError

from cvopt.domain.enums import ClaimType, Fidelity, MatchLevel, Provability
from cvopt.domain.records import Claim, Position
from cvopt.domain.review import CreateClaim, Note, Payload, ProposalItem


def test_enum_parsing_is_case_insensitive() -> None:
    assert Provability("HIGH") is Provability.HIGH
    assert TypeAdapter(Fidelity).validate_python(" Exaggerated ") is Fidelity.EXAGGERATED
    assert TypeAdapter(MatchLevel).validate_json('"Semantic"') is MatchLevel.SEMANTIC
    with pytest.raises(ValidationError):
        TypeAdapter(ClaimType).validate_python("hobby")


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    p = Position(id="p1", employer="Acme", title="Dev", start_month="2020-01", end_month=None)
    with pytest.raises(ValidationError):
        p.title = "Boss"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Position.model_validate({**p.model_dump(), "extra": 1})


@pytest.mark.parametrize("month", ["2020-13", "2020-1", "20-01", "2020-00"])
def test_month_format(month: str) -> None:
    with pytest.raises(ValidationError):
        Position(id="p", employer="A", title="T", start_month=month, end_month=None)


def test_claim_parses_llm_casing() -> None:
    c = Claim.model_validate(
        {
            "id": "c",
            "type": "Skill",
            "text": "Python",
            "text_lang": "en",
            "position_id": None,
            "keywords": ["python"],
            "status": "CONFIRMED",
            "origin": "manual",
            "provability": "Medium",
        }
    )
    assert c.type is ClaimType.SKILL and c.provability is Provability.MEDIUM


def test_payload_is_discriminated_by_action() -> None:
    payload = TypeAdapter(Payload).validate_python(
        {
            "action": "create_claim",
            "type": "skill",
            "text": "Rust",
            "text_lang": "en",
            "position_id": "$item:1",
            "keywords": ["rust"],
            "evidence": {"kind": "own_cv"},
        }
    )
    assert isinstance(payload, CreateClaim)
    item = ProposalItem(id="i", batch_id="b", seq=2, payload=Note(text="faint praise"))
    roundtrip = ProposalItem.model_validate_json(item.model_dump_json())
    assert roundtrip == item and roundtrip.action == "note"
    with pytest.raises(ValidationError):
        TypeAdapter(Payload).validate_python({"action": "note", "text": "x", "bogus": 1})
