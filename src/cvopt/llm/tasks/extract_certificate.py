"""extract_certificate: one education or certificate record and the skills it attests."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import (
    ExCertificate,
    ExEducation,
    ExSkillClaim,
    check_claim_text,
    check_education,
    check_month,
    tagged,
)
from cvopt.ports import FilePart, TextPart


class CertificateInput(Model):
    text: str
    language: str
    kind: str  # diploma or certificate, as declared by the user


class CertificateOutput(Model):
    record_type: Literal["education", "certificate"]
    education: ExEducation  # filled when record_type is education
    certificate: ExCertificate  # filled when record_type is certificate
    claims: list[ExSkillClaim]


def render(inp: CertificateInput) -> list[TextPart | FilePart]:
    return [
        TextPart(
            f"Declared kind: {inp.kind}\nDocument language: {inp.language}\n\n"
            + tagged("document", inp.text)
        )
    ]


def check(inp: CertificateInput, out: CertificateOutput) -> None:
    if out.record_type == "education":
        check_education(out.education, "education")
    else:
        if not out.certificate.name:
            raise ValueError("certificate.name is required")
        check_month(out.certificate.issued_month, "certificate.issued_month")
    for i, c in enumerate(out.claims):
        check_claim_text(c.text, c.keywords, f"claims[{i}]")


EXTRACT_CERTIFICATE = Task(
    name="extract_certificate",
    version="1",
    tier="strong",
    input_model=CertificateInput,
    output_model=CertificateOutput,
    max_tokens=2000,
    render=render,
    check=check,
)
