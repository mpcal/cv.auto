"""Historical models parse absent quotes; current extraction requires real quotations."""

import pytest

from cvopt.llm.tasks.extract_certificate import (
    EXTRACT_CERTIFICATE,
    CertificateInput,
    CertificateOutput,
)
from cvopt.llm.tasks.extract_cv import EXTRACT_CV, CVInput, CVOutput
from cvopt.llm.tasks.extract_reference_letter import (
    EXTRACT_REFERENCE_LETTER,
    LetterInput,
    LetterOutput,
)

PROFILE = dict(name="", email="", phone="", city="", country="", website="", profiles=[])
EDUCATION = dict(
    institution="Uni",
    area="CS",
    degree_type="MSc",
    degree_level="master",
    start_month="",
    end_month="",
    grade="",
)
CERT = dict(name="Rust", issuer="Issuer", issued_month="", url="")
POSITION = dict(
    employer="Acme", employer_location="", title="Dev", start_month="2020-01", end_month=""
)


@pytest.mark.parametrize(
    "collection,record",
    [
        ("claims", dict(type="skill", text="Rust", keywords=["rust"], position=-1)),
        ("positions", POSITION),
        ("education", EDUCATION),
        ("languages", dict(language_code="en", level="B2")),
        ("certificates", CERT),
        ("awards", dict(title="Prize", awarder="Uni", awarded_month="", summary="")),
    ],
)
def test_cv_requires_quotes_for_new_evidence_but_parses_legacy(collection, record):
    data = dict(
        profile=PROFILE,
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        claims=[],
    )
    data[collection] = [record]
    legacy = CVOutput.model_validate(data)
    inp = CVInput(text="Rust Uni Acme Prize English", language="en")
    with pytest.raises(ValueError, match="excerpt"):
        EXTRACT_CV.check(inp, legacy)
    for quote in ["Invented quote", "   "]:
        bad = CVOutput.model_validate({**data, collection: [{**record, "excerpt": quote}]})
        with pytest.raises(ValueError, match="excerpt"):
            EXTRACT_CV.check(inp, bad)
    quoted = CVOutput.model_validate({**data, collection: [{**record, "excerpt": "Rust"}]})
    EXTRACT_CV.check(inp, quoted)


def test_certificate_requires_record_and_claim_quotes():
    data = dict(
        record_type="certificate",
        education=EDUCATION,
        certificate=CERT,
        claims=[dict(text="Rust", keywords=["rust"])],
    )
    inp = CertificateInput(text="Rust", language="en", kind="certificate")
    legacy = CertificateOutput.model_validate(data)
    with pytest.raises(ValueError, match="excerpt"):
        EXTRACT_CERTIFICATE.check(inp, legacy)
    data["certificate"] = {**CERT, "excerpt": "Rust"}
    with pytest.raises(ValueError, match="excerpt"):
        EXTRACT_CERTIFICATE.check(inp, CertificateOutput.model_validate(data))
    data["claims"][0]["excerpt"] = "Rust"
    EXTRACT_CERTIFICATE.check(inp, CertificateOutput.model_validate(data))


def test_empty_reference_letter_quote_is_not_evidence():
    out = LetterOutput.model_validate(
        dict(
            issuer="Acme",
            author="Carol",
            period_start="",
            period_end="",
            position_ref="",
            new_position=POSITION,
            passages=[
                dict(
                    excerpt="",
                    grade="good",
                    claims=[dict(type="skill", text="Rust", keywords=["rust"])],
                )
            ],
        )
    )
    with pytest.raises(ValueError, match="excerpt"):
        EXTRACT_REFERENCE_LETTER.check(LetterInput(text="Rust", language="en", positions=[]), out)
