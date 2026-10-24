"""The scoring engine: pure functions, with match values supplied directly."""

import math
from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from cvopt.domain.cv import CVElement, JSONResume, Placement, parse_elements, to_month
from cvopt.domain.enums import ElementKind as K
from cvopt.domain.enums import Importance, MatchLevel
from cvopt.domain.enums import RequirementCategory as C
from cvopt.domain.jd import Requirement
from cvopt.domain.scoring import ScoringParams, score, text_level, years_credit

AS_OF = date(2026, 6, 30)
P = ScoringParams()


def req(
    id: str, cat: C = C.HARD_SKILL, imp: Importance = Importance.MUST, years: float | None = None
) -> Requirement:
    return Requirement(
        id=id,
        text=id,
        category=cat,
        importance=imp,
        keywords=["Python"],
        synonyms=["Py"],
        years=years,
    )


def hl(id: str, ref: str, start: str, end: str | None) -> CVElement:
    return CVElement(
        id=id,
        kind=K.HIGHLIGHT,
        text=id,
        position_ref=ref,
        start_month=start,
        end_month=end,
        placement=1.0,
    )


def test_text_level_keyword_needs_judge_agreement() -> None:
    r = req("R1")
    assert text_level(r, "Built tools in Python", MatchLevel.PARTIAL) is MatchLevel.EXACT
    assert text_level(r, "Py scripts", MatchLevel.SEMANTIC) is MatchLevel.SYNONYM
    assert text_level(r, "Python snake keeper", MatchLevel.NONE) is MatchLevel.NONE
    assert text_level(r, "Coding", MatchLevel.SEMANTIC) is MatchLevel.SEMANTIC


def test_score_weights_placement_and_ties() -> None:
    els = [
        CVElement(id="a", kind=K.SKILL, text="a", placement=0.7),
        CVElement(id="b", kind=K.HIGHLIGHT, text="b", placement=1.0),
        CVElement(id="c", kind=K.HIGHLIGHT, text="c", placement=1.0),
    ]
    reqs = [req("R1"), req("R2", imp=Importance.NICE), req("R3", cat=C.LOGISTICS)]
    vals = {("R1", "a"): 1.0, ("R1", "b"): 0.6, ("R2", "b"): 0.3, ("R2", "c"): 0.3}
    res = score(els, reqs, lambda r, e: vals.get((r, e), 0.0), P, AS_OF)
    r1, r2 = res.requirements  # logistics are not scored
    assert math.isclose(r1.coverage, 0.7) and r1.winners == {"a": 1.0}
    assert r2.winners == {"b": 0.5, "c": 0.5}
    assert math.isclose(res.total, 100 * (3 * 0.7 + 1 * 0.3) / 4)
    assert math.isclose(res.impacts["b"], res.impacts["c"])


def test_years_credit_latest_start_wins_months() -> None:
    els = [
        hl("old", "/work/1", "2018-01", "2020-12"),
        hl("new", "/work/0", "2020-01", None),
        hl("new2", "/work/0", "2020-01", None),
    ]
    vals = {("R", "old"): 0.6, ("R", "new"): 0.6, ("R", "new2"): 1.0}
    cov, shares = years_credit(
        req("R", C.YEARS_EXPERIENCE, years=10), els, lambda r, e: vals.get((r, e), 0.0), P, AS_OF
    )
    union = 24 + 78  # 2018-01..2019-12 by the old position, 2020-01..2026-06 by the new
    assert math.isclose(cov, union / 12 / 10)
    assert set(shares) == {"old", "new2"}  # the best highlight of each position
    assert math.isclose(shares["new2"], 78 / union)
    weak = {("R", "old"): 0.3}
    assert years_credit(
        req("R", C.YEARS_EXPERIENCE, years=1), els, lambda r, e: weak.get((r, e), 0.0), P, AS_OF
    ) == (0.0, {})


elements = st.lists(
    st.builds(
        lambda i, k, p: CVElement(id=f"e{i}", kind=k, text="t", placement=p),
        st.integers(0, 30),
        st.sampled_from([K.SKILL, K.HIGHLIGHT, K.SUMMARY, K.HEADLINE]),
        st.sampled_from([0.7, 0.8, 0.85, 1.0]),
    ),
    min_size=1,
    max_size=12,
    unique_by=lambda e: e.id,
)
requirements = st.lists(
    st.builds(
        lambda i, c, imp: req(f"R{i}", c, imp),
        st.integers(0, 20),
        st.sampled_from([C.HARD_SKILL, C.SOFT_SKILL, C.EXPERIENCE, C.DOMAIN]),
        st.sampled_from(Importance),
    ),
    min_size=1,
    max_size=8,
    unique_by=lambda r: r.id,
)
levels = st.sampled_from([0.0, 0.3, 0.6, 0.9, 1.0])


@given(elements, requirements, st.data())
def test_impacts_plus_gaps_equal_100(els, reqs, data) -> None:  # type: ignore[no-untyped-def]
    vals = {(r.id, e.id): data.draw(levels) for r in reqs for e in els}
    res = score(els, reqs, lambda r, e: vals[r, e], P, AS_OF)
    gaps = sum(r.gap_points for r in res.requirements)
    assert math.isclose(sum(res.impacts.values()) + gaps, 100)
    assert math.isclose(res.total, sum(res.impacts.values()))
    assert all(0 <= r.coverage <= 1 for r in res.requirements)
    assert res == score(els, reqs, lambda r, e: vals[r, e], P, AS_OF)


def test_parse_elements_ids_dates_placement() -> None:
    cv = JSONResume.model_validate(
        {
            "basics": {"label": "Data Scientist", "summary": "One sentence. Two, e.g. this one."},
            "work": [
                {"startDate": "2015", "endDate": "2019-05-03", "highlights": ["Old"]},
                {"startDate": "2020-01", "highlights": ["New"]},
            ],
            "skills": [{"keywords": ["Python"]}],
            "education": [{"institution": "ETH", "area": "CS", "studyType": "MSc"}],
            "languages": [{"language": "Deutsch", "fluency": "fließend"}],
            "certificates": [{"name": "AWS SA"}],
            "unknownSection": [],
        }
    )
    els = {e.id: e for e in parse_elements(cv, AS_OF, "en", Placement())}
    assert list(els) == [
        "/basics/label",
        "/basics/summary#0",
        "/basics/summary#1",
        "/work/0/highlights/0",
        "/work/1/highlights/0",
        "/skills/0/keywords/0",
        "/education/0",
        "/languages/0",
        "/certificates/0",
    ]
    old, new = els["/work/0/highlights/0"], els["/work/1/highlights/0"]
    assert (old.start_month, old.end_month, old.placement) == ("2015-01", "2019-05", 0.85)
    assert (new.end_month, new.placement) == (None, 1.0)
    assert els["/skills/0/keywords/0"].placement == 0.7
    assert els["/languages/0"].meta == {"language_code": "de", "level": "C1"}
    assert els["/education/0"].meta["degree_level"] == "master"
    assert to_month("2021") == "2021-01" and to_month("nonsense") is None
