"""Rule checks over a finished CV's statements (P1 §6–7, P2 D10). Tests reuse them."""

from collections import Counter
from collections.abc import Sequence

from cvopt.domain.base import Model
from cvopt.domain.candidates import Annotation, Candidate
from cvopt.domain.enums import Fidelity, Provability
from cvopt.domain.records import Claim
from cvopt.domain.rules import Vocabulary, passes_hard_facts, statement_provability
from cvopt.domain.text import normalize


class PlacedStatement(Model):
    pointer: str
    sentence: int | None
    text: str
    candidate: Candidate
    sources: list[Claim]  # in the JD's language
    inferable: bool
    gain: float
    alternative_gain: float | None  # exaggerated: best non-exaggerated, same sources


class Violation(Model):
    candidate_id: str
    rule: str
    message: str


def validate_statements(
    placed: Sequence[PlacedStatement],
    annotations: Sequence[Annotation],
    vocab: Vocabulary,
    min_exaggeration_gain: float,
) -> list[Violation]:
    out: list[Violation] = []
    keys = Counter((a.pointer, a.sentence) for a in annotations)
    by_key = {(a.pointer, a.sentence): a for a in annotations}
    statement_keys = {(p.pointer, p.sentence) for p in placed}
    for key in keys:
        if key not in statement_keys:
            out.append(
                Violation(
                    candidate_id="",
                    rule="annotation",
                    message=f"annotation {key} points to no statement",
                )
            )
    assertion_keys = Counter(p.candidate.assertion_key for p in placed if p.candidate.assertion_key)
    for p in placed:
        c = p.candidate

        def bad(rule: str, message: str, c: Candidate = c) -> None:
            out.append(Violation(candidate_id=c.id, rule=rule, message=message))

        a = by_key.get((p.pointer, p.sentence))
        if (
            keys[(p.pointer, p.sentence)] != 1
            or a is None
            or normalize(a.text) != normalize(p.text)
        ):
            bad("annotation", f"{p.pointer} needs exactly one matching annotation")
        elif (a.fidelity, a.provability) != (c.fidelity, c.provability):
            bad("annotation", f"{p.pointer}: annotation disagrees with the statement")
        if c.fidelity is not Fidelity.INVENTED and not p.sources:
            bad("sources", "a sourced statement needs source claims")
        if not passes_hard_facts(p.text, c.fidelity, p.sources, vocab):
            bad(
                "hard_facts",
                "a number, date or name is missing from the sources"
                if c.fidelity is not Fidelity.INVENTED
                else "an invented statement has a figure",
            )
        if c.fidelity is Fidelity.EXAGGERATED and (
            p.alternative_gain is None or p.gain < p.alternative_gain + min_exaggeration_gain - 1e-9
        ):
            bad("exaggeration", "does not beat its best rephrased alternative by the minimum gain")
        if c.assertion_key and assertion_keys[c.assertion_key] > 1:
            bad("assertion", f"invented assertion {c.assertion_key} appears twice")
        expected = statement_provability(
            c.fidelity,
            [s.provability for s in p.sources] or [Provability.NONE],
            inferable=p.inferable,
        )
        if c.provability is not expected:
            bad("provability", f"provability {c.provability} should be {expected}")
    return out
