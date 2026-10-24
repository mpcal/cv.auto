import pytest
from hypothesis import given
from hypothesis import strategies as st

from cvopt.domain.enums import (
    PROVABILITY_RANK,
    DegreeLevel,
    DocumentKind,
    EvidenceKind,
    Fidelity,
    MatchLevel,
    Provability,
)
from cvopt.domain.records import Position
from cvopt.domain.rules import (
    cefr_level,
    claim_provability,
    degree_level,
    education_match,
    fact_record_provability,
    language_code,
    language_match,
    more_altered,
    order_positions,
    statement_provability,
)

H, M, L, N = Provability.HIGH, Provability.MEDIUM, Provability.LOW, Provability.NONE
E = EvidenceKind


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        ([E.OWN_CV, E.REFERENCE_LETTER], H),
        ([E.REFERENCE_PERSON], H),
        ([E.CERTIFICATE_OR_DIPLOMA], H),
        ([E.SELF_DECLARED, E.LEARNABLE_DECLARED], M),
        ([E.LEARNABLE_DECLARED], L),
        ([], N),
    ],
)
def test_claim_provability(kinds: list[EvidenceKind], expected: Provability) -> None:
    assert claim_provability(kinds) is expected


@given(st.lists(st.sampled_from(EvidenceKind)), st.sampled_from(EvidenceKind))
def test_provability_never_drops_when_evidence_is_added(
    kinds: list[EvidenceKind], extra: EvidenceKind
) -> None:
    before = claim_provability(kinds)
    after = claim_provability([*kinds, extra])
    assert PROVABILITY_RANK[after] >= PROVABILITY_RANK[before]


def test_fact_record_provability() -> None:
    assert fact_record_provability([DocumentKind.CV, DocumentKind.DIPLOMA]) is H
    assert fact_record_provability([DocumentKind.CV]) is M
    assert fact_record_provability([]) is M


@pytest.mark.parametrize(
    ("fidelity", "row"),
    [
        (Fidelity.UNMODIFIED, (H, M, L)),
        (Fidelity.REPHRASED, (H, M, L)),
        (Fidelity.EXAGGERATED, (M, L, L)),
    ],
)
def test_statement_matrix(fidelity: Fidelity, row: tuple[Provability, ...]) -> None:
    assert tuple(statement_provability(fidelity, [s]) for s in (H, M, L)) == row


def test_invented_and_multiple_sources() -> None:
    assert statement_provability(Fidelity.INVENTED, [], inferable=True) is L
    assert statement_provability(Fidelity.INVENTED, [H]) is N
    assert statement_provability(Fidelity.REPHRASED, [H, M, H]) is M
    assert statement_provability(Fidelity.EXAGGERATED, [H, L]) is L
    with pytest.raises(ValueError):
        statement_provability(Fidelity.UNMODIFIED, [])


def test_more_altered() -> None:
    assert more_altered(Fidelity.REPHRASED, Fidelity.EXAGGERATED) is Fidelity.EXAGGERATED
    assert more_altered(Fidelity.INVENTED, Fidelity.UNMODIFIED) is Fidelity.INVENTED


def _pos(id: str, start: str, end: str | None) -> Position:
    return Position(id=id, employer="X", title="T", start_month=start, end_month=end)


def test_order_positions() -> None:
    ps = [
        _pos("old", "2010-01", "2012-06"),
        _pos("tie_early", "2015-01", "2018-12"),
        _pos("cur_early", "2019-01", None),
        _pos("tie_late", "2016-01", "2018-12"),
        _pos("cur_late", "2021-03", None),
    ]
    assert [p.id for p in order_positions(ps)] == [
        "cur_late",
        "cur_early",
        "tie_late",
        "tie_early",
        "old",
    ]


months = st.builds(lambda y, m: f"{y}-{m:02d}", st.integers(2000, 2030), st.integers(1, 12))


@given(st.lists(st.tuples(months, st.one_of(st.none(), months)), max_size=8))
def test_order_positions_properties(ranges: list[tuple[str, str | None]]) -> None:
    ps = [_pos(f"p{i}", s, e) for i, (s, e) in enumerate(ranges)]
    ordered = order_positions(ps)
    assert sorted(p.id for p in ordered) == sorted(p.id for p in ps)
    ends = [p.end_month for p in ordered]
    n_current = ends.count(None)
    assert all(e is None for e in ends[:n_current])
    closed = [e for e in ends[n_current:] if e is not None]
    assert closed == sorted(closed, reverse=True)
    assert order_positions(reversed(ps)) == ordered


@pytest.mark.parametrize(
    ("held", "required", "value"),
    [
        ("C2", "C1", 1.0),
        ("native", "C2", 1.0),
        ("C1", "C1", 1.0),
        ("B2", "C1", 0.5),
        ("B1", "C1", 0.0),
        ("C2", "native", 0.5),
        ("c1", "B2", 1.0),
    ],
)
def test_language_match(held: str, required: str, value: float) -> None:
    assert language_match(held, required) == value


def test_language_tables() -> None:
    assert language_code("Deutsch") == language_code("German") == "de"
    assert language_code("FR") == "fr"
    assert language_code("Klingon") is None
    assert cefr_level("Fließend") == "C1"
    assert cefr_level("Muttersprache") == cefr_level("native") == "native"
    assert cefr_level("b2") == "B2"
    assert cefr_level("so-so") is None


@pytest.mark.parametrize(
    ("name", "level"),
    [
        ("MSc", DegreeLevel.MASTER),
        ("M.Sc.", DegreeLevel.MASTER),
        ("Dr. rer. nat.", DegreeLevel.DOCTORATE),
        ("PhD", DegreeLevel.DOCTORATE),
        ("BSc Computer Science", DegreeLevel.BACHELOR),
        ("Abitur", DegreeLevel.SECONDARY),
        ("Ausbildung", DegreeLevel.VOCATIONAL),
        ("Certificate of Attendance", DegreeLevel.OTHER),
    ],
)
def test_degree_level(name: str, level: DegreeLevel) -> None:
    assert degree_level(name) is level


D, ML = DegreeLevel, MatchLevel


@pytest.mark.parametrize(
    ("held", "required", "field", "value"),
    [
        (D.DOCTORATE, D.MASTER, ML.EXACT, 1.0),
        (D.MASTER, D.MASTER, ML.SEMANTIC, 1.0),
        (D.MASTER, D.MASTER, ML.PARTIAL, 0.6),
        (D.MASTER, D.MASTER, ML.NONE, 0.0),
        (D.MASTER, D.DOCTORATE, ML.EXACT, 0.3),
        (D.BACHELOR, D.DOCTORATE, ML.EXACT, 0.0),
        (D.OTHER, D.BACHELOR, ML.EXACT, 0.0),
        (D.SECONDARY, None, ML.EXACT, 1.0),
    ],
)
def test_education_match(
    held: DegreeLevel, required: DegreeLevel | None, field: MatchLevel, value: float
) -> None:
    assert education_match(held, required, field) == value
