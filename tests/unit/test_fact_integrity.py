"""Synthetic regression examples for original-fact protection (no provider captures)."""

import pytest

from cvopt.domain.enums import ClaimStatus, ClaimType, Fidelity, Origin, Provability
from cvopt.domain.records import Claim
from cvopt.domain.rules import Vocabulary, passes_hard_facts
from cvopt.llm.tasks.translate import TRANSLATE, TextRef, TranslateInput, TranslateOutput


def claim(text: str, keywords: list[str] | None = None) -> Claim:
    return Claim(
        id="c1",
        type=ClaimType.ACHIEVEMENT,
        text=text,
        text_lang="en",
        position_id=None,
        keywords=keywords or [],
        status=ClaimStatus.CONFIRMED,
        origin=Origin.MANUAL,
        provability=Provability.MEDIUM,
    )


@pytest.mark.parametrize("text", ["Led two teams", "Led zwei Teams", "Led twenty teams"])
def test_inventions_cannot_contain_spelled_quantities(text: str) -> None:
    assert not passes_hard_facts(text, Fidelity.INVENTED, [], Vocabulary([]))


@pytest.mark.parametrize(
    ("text", "verified_names"),
    [
        ("Worked for Seven Seas", ["Seven Seas"]),
        ("Used OneDrive", []),
        ("Advised Twenty One Capital", ["Twenty One Capital"]),
    ],
)
def test_numeric_words_in_names_are_not_quantities(text: str, verified_names: list[str]) -> None:
    assert passes_hard_facts(
        text,
        Fidelity.INVENTED,
        [],
        Vocabulary([]),
        numeric_name_spans=verified_names,
    )


def test_keywords_do_not_authorize_new_assertions() -> None:
    assert not passes_hard_facts(
        "Used Kubernetes",
        Fidelity.REPHRASED,
        [claim("Developed services", ["Kubernetes"])],
        Vocabulary([["Kubernetes"]]),
    )


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        ("Managed two FTE", "Leitete zwanzig FTE"),
        ("Managed 2 FTE", "Leitete 200 FTE"),
    ],
)
def test_translation_check_rejects_changed_quantities(source: str, translated: str) -> None:
    assert TRANSLATE.check is not None
    with pytest.raises(ValueError):
        TRANSLATE.check(
            TranslateInput(texts=[TextRef(ref="T1", text=source)], target_language="de"),
            TranslateOutput(translations=[TextRef(ref="T1", text=translated)]),
        )


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        ("Managed two FTE", "Leitete zwei FTE"),
        ("Saved 1,000 hours", "Sparte 1.000 Stunden"),
        ("Saved 2.5 hours", "Sparte 2,5 Stunden"),
    ],
)
def test_translation_check_accepts_equivalent_localized_quantities(
    source: str,
    translated: str,
) -> None:
    assert TRANSLATE.check is not None
    TRANSLATE.check(
        TranslateInput(texts=[TextRef(ref="T1", text=source)], target_language="de"),
        TranslateOutput(translations=[TextRef(ref="T1", text=translated)]),
    )


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        ("Led a team of 6", "Leitete ein Team von 6"),
        ("Led one team", "Leitete genau ein Team"),
        ("Saved one hundred and two hours", "Sparte hundertzwei Stunden"),
        ("Used TLS 1.2.3", "Verwendete TLS 1.2.3"),
        ("Worked from 2020-01", "Arbeitete seit 2020-01"),
    ],
)
def test_translation_numeric_guard_handles_articles_compounds_and_versions(
    source: str,
    translated: str,
) -> None:
    assert TRANSLATE.check is not None
    TRANSLATE.check(
        TranslateInput(texts=[TextRef(ref="T1", text=source)], target_language="de"),
        TranslateOutput(translations=[TextRef(ref="T1", text=translated)]),
    )


@pytest.mark.parametrize("text", ["Zwei Standorte modernisiert", "Twenty Offices managed"])
def test_capitalized_counted_nouns_are_explicit_invented_quantities(text: str) -> None:
    assert not passes_hard_facts(text, Fidelity.INVENTED, [], Vocabulary([]))


def test_sourced_article_one_ambiguity_matches_translation_guard() -> None:
    from cvopt.domain.facts import translation_quantities_match

    source, display = "Leitete ein Team", "Led one team"
    assert translation_quantities_match(source, display)
    assert passes_hard_facts(display, Fidelity.UNMODIFIED, [claim(source)], Vocabulary([]))


def test_spelled_numeric_name_without_verified_span_fails_closed() -> None:
    assert not passes_hard_facts("Worked for Seven Seas", Fidelity.INVENTED, [], Vocabulary([]))


@pytest.mark.parametrize(
    ("text", "spans"),
    [
        ("Advised Seven Seas with two teams", ["Seven Seas"]),
        ("Used Windows 11", ["Windows 11"]),
        ("Advised Seven Seas", [""]),
        ("Advised Seven Seas", ["Other Name"]),
    ],
)
def test_verified_numeric_names_do_not_hide_other_counts_or_invalid_spans(
    text: str,
    spans: list[str],
) -> None:
    assert not passes_hard_facts(
        text,
        Fidelity.INVENTED,
        [],
        Vocabulary([]),
        numeric_name_spans=spans,
    )


@pytest.mark.parametrize(
    ("source", "translated"),
    [
        ("Managed two million CHF", "Verwaltete 2 Millionen CHF"),
        ("Managed 2.5 million CHF", "Verwaltete 2.500.000 CHF"),
        ("Managed 2,5 Millionen CHF", "Managed 2,500,000 CHF"),
        ("Served 2,000 users", "Betreute 2 000 Nutzer"),
        ("Managed 2,500,000.50 CHF", "Verwaltete 2\u00a0500\u00a0000,50 CHF"),
        ("Managed 2,000.500 CHF", "Verwaltete 2 000,500 CHF"),
        ("Total users: 2,000.", "Nutzer insgesamt: 2 000."),
        ("Served 2,000, then stopped", "Betreute 2 000, dann beendet"),
        ("Served 12,345 users", "Betreute 12\u2009345 Nutzer"),
        ("Served 123,456 users", "Betreute 123\u202f456 Nutzer"),
        ("Managed three billion CHF", "Verwaltete 3 Milliarden CHF"),
        ("Managed 2 hundred CHF", "Verwaltete zweihundert CHF"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_complete_quantity_expressions_pass_translation_and_sourced_guards(
    source, translated, reverse
):
    if reverse:
        source, translated = translated, source
    assert TRANSLATE.check is not None
    TRANSLATE.check(
        TranslateInput(
            texts=[TextRef(ref="T1", text=source)], target_language="en" if reverse else "de"
        ),
        TranslateOutput(translations=[TextRef(ref="T1", text=translated)]),
    )
    assert passes_hard_facts(translated, Fidelity.REPHRASED, [claim(source)], Vocabulary([]))


@pytest.mark.parametrize(
    ("source", "changed"),
    [
        ("Saved 2 million CHF", "Saved 2.5 million CHF"),
        ("Saved 2 million CHF", "Saved 2 CHF and one million CHF"),
        ("Served 2 000 users", "Served 2 001 users"),
        ("Managed 2 000,500 CHF", "Managed 2,000,500 CHF"),
        ("Led 2 teams and 300 users", "Led 2 300 users"),
        ("Counts 2 30", "Count 230"),
        ("Counts 2\n300", "Count 2300"),
        ("Counts 12 3456", "Count 123456"),
        ("Used TLS 1.2.3 with 400 users", "Used TLS 1.2.3400"),
        ("Used TLS 1.2.3 400 times", "Used TLS 1.2.3400 times"),
        ("Worked from 2020-01 with 200 users", "Worked from 2020-01200"),
        ("Worked from 2020-01 200 times", "Worked from 2020-01200 times"),
    ],
)
def test_quantity_normalization_does_not_authorize_changed_or_joined_values(source, changed):
    assert TRANSLATE.check is not None
    with pytest.raises(ValueError):
        TRANSLATE.check(
            TranslateInput(texts=[TextRef(ref="T1", text=source)], target_language="de"),
            TranslateOutput(translations=[TextRef(ref="T1", text=changed)]),
        )
    assert not passes_hard_facts(changed, Fidelity.REPHRASED, [claim(source)], Vocabulary([]))
