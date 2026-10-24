from hypothesis import given
from hypothesis import strategies as st

from cvopt.domain.text import contains_term, normalize, split_sentences


def test_normalize() -> None:
    assert normalize("  Machine–Learning\n ENGINEER ") == "machine-learning engineer"


def test_contains_term_whole_word() -> None:
    assert contains_term("Python", "Built pipelines in python and SQL.")
    assert not contains_term("Java", "Wrote JavaScript.")
    assert contains_term("C++", "Ten years of C++ development")
    assert contains_term("machine-learning", "Machine‑learning models")
    assert not contains_term("ML", "HTML pages")


def test_split_sentences_english() -> None:
    text = "Led a team of 5, e.g. Dr. Smith. Shipped v2.1 on time! Next came growth."
    assert split_sentences(text, "en") == [
        "Led a team of 5, e.g. Dr. Smith.",
        "Shipped v2.1 on time!",
        "Next came growth.",
    ]


def test_split_sentences_german() -> None:
    text = "Er leitete z.B. das Projekt bzw. die Abteilung. Über 3 Jahre war er tätig."
    assert split_sentences(text, "de") == [
        "Er leitete z.B. das Projekt bzw. die Abteilung.",
        "Über 3 Jahre war er tätig.",
    ]


def test_split_sentences_empty() -> None:
    assert split_sentences("   ", "en") == []


@given(
    st.lists(
        st.from_regex(r"[A-Z][a-z]{1,8}( [a-z]{1,8}){0,4}\.", fullmatch=True),
        min_size=1,
        max_size=5,
    )
)
def test_split_roundtrip(sentences: list[str]) -> None:
    assert split_sentences(" ".join(sentences), "en") == sentences
