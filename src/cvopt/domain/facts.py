"""Deterministic numeric guards; semantic entity/relationship checks live in LLM tasks."""

import re
from collections import Counter
from decimal import Decimal, InvalidOperation

_GROUP_SPACES = " \u00a0\u2009\u202f"
_DIGITS = re.compile(
    # Only complete, conventional groups qualify. Do not group the last component
    # of a date/version, join counts across lines, or consume part of a longer count.
    rf"(?<![\w.,/'’:\-])\d{{1,3}}(?:[{_GROUP_SPACES}]\d{{3}})+(?:[.,]\d+)?"
    r"(?!\d|[.,/'’:\-]\d)|\d+(?:[.,'’]\d+)*"
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SMALL: dict[str, int] = dict(
    zip(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
            "thirty",
            "forty",
            "fifty",
            "sixty",
            "seventy",
            "eighty",
            "ninety",
        ],
        [*range(21), 30, 40, 50, 60, 70, 80, 90],
        strict=True,
    )
)
_SMALL.update(
    dict(
        zip(
            [
                "null",
                "ein",
                "eins",
                "eine",
                "einen",
                "einem",
                "einer",
                "zwei",
                "drei",
                "vier",
                "fünf",
                "sechs",
                "sieben",
                "acht",
                "neun",
                "zehn",
                "elf",
                "zwölf",
                "dreizehn",
                "vierzehn",
                "fünfzehn",
                "sechzehn",
                "siebzehn",
                "achtzehn",
                "neunzehn",
                "zwanzig",
                "dreißig",
                "vierzig",
                "fünfzig",
                "sechzig",
                "siebzig",
                "achtzig",
                "neunzig",
            ],
            [0, 1, 1, 1, 1, 1, 1, *range(2, 21), 30, 40, 50, 60, 70, 80, 90],
            strict=True,
        )
    )
)
_SCALE = {
    "hundred": 100,
    "hundert": 100,
    "thousand": 1000,
    "tausend": 1000,
    "million": 1000000,
    "millionen": 1000000,
    "billion": 1000000000,
    "milliarde": 1000000000,
    "milliarden": 1000000000,
}


def _word_number(word: str) -> int | None:
    if word in _SMALL:
        return _SMALL[word]
    for scale in ("tausend", "hundert"):
        if scale in word:
            head, tail = word.split(scale, 1)
            left = _word_number(head) if head else 1
            right = _word_number(tail) if tail else 0
            if left is not None and right is not None:
                return left * _SCALE[scale] + right
    if "und" in word:
        head, tail = word.split("und", 1)
        if head in _SMALL and tail in _SMALL and _SMALL[head] < 10 <= _SMALL[tail]:
            return _SMALL[head] + _SMALL[tail]
    return _SCALE.get(word)


def _decimal(token: str) -> Decimal | str:
    original = token
    space_grouped = any(space in token for space in _GROUP_SPACES)
    token = token.translate(str.maketrans("", "", _GROUP_SPACES))
    token = token.replace("'", "").replace("’", "")
    if space_grouped:
        # Grouping is already explicit; a remaining separator marks decimals even
        # with three decimal places (2 000,500 is 2000.5, never 2000500).
        token = token.replace(",", ".")
    elif "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        token = token.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in token or "." in token:
        separator = "," if "," in token else "."
        parts = token.split(separator)
        token = (
            "".join(parts) if all(len(p) == 3 for p in parts[1:]) else token.replace(separator, ".")
        )
    try:
        return Decimal(token)
    except InvalidOperation:
        return original  # version/date-like tokens stay exact, rather than crashing


_ARTICLE = re.compile(r"\b(?:ein|eine|einen|einem|einer)\b", re.IGNORECASE)
_DIGIT_SCALE = re.compile(rf"[{_GROUP_SPACES}]+({'|'.join(_SCALE)})\b", re.IGNORECASE)


def quantities(text: str) -> Counter[Decimal | str]:
    """EN/DE digit and spelled quantities, normalized across localized separators.

    Numeric words are counted regardless of capitalization. Proper-name exceptions
    require explicit verified spans outside this parser.
    """
    out: Counter[Decimal | str] = Counter()
    consumed_scales: set[int] = set()
    for match in _DIGITS.finditer(text):
        quantity = _decimal(match.group())
        scale = _DIGIT_SCALE.match(text, match.end())
        if isinstance(quantity, Decimal) and scale is not None:
            quantity *= _SCALE[scale[1].casefold()]
            consumed_scales.add(scale.start(1))
        out[quantity] += 1
    words = [m for m in _WORD.finditer(text) if m.start() not in consumed_scales]
    i = 0
    while i < len(words):
        match = words[i]
        word = match.group()
        number = _word_number(word.casefold())
        if _ARTICLE.fullmatch(word) and not re.search(
            r"\b(?:genau|nur|exakt)\s+$", text[: match.start()], re.IGNORECASE
        ):
            # German indefinite articles need semantic context, not a numeric assertion.
            i += 1
            continue
        if number is None:
            i += 1
            continue
        current, total = number, 0
        j = i + 1
        while j < len(words):
            gap = text[words[j - 1].end() : words[j].start()]
            if gap.strip(" -"):
                break
            token = words[j].group().casefold()
            if (
                token in ("and", "und")
                and current >= 100
                and j + 1 < len(words)
                and _word_number(words[j + 1].group().casefold()) is not None
            ):
                j += 1
                continue
            value = _word_number(token)
            if value is None:
                break
            if token in _SCALE:
                current *= value
                if value >= 1000:
                    total += current
                    current = 0
            else:
                current += value
            j += 1
        out[Decimal(total + current)] += 1
        i = j
    return out


def _comparison_quantities(
    source: str, text: str
) -> tuple[Counter[Decimal | str], Counter[Decimal | str]]:
    before, after = quantities(source), quantities(text)
    if _ARTICLE.search(source) or _ARTICLE.search(text):
        before.pop(Decimal(1), None)
        after.pop(Decimal(1), None)
    return before, after


def translation_quantities_match(source: str, translated: str) -> bool:
    """Compare quantities, deferring ambiguous German article/one uses to context."""
    before, after = _comparison_quantities(source, translated)
    return before == after


def sourced_quantities_match(source: str, text: str) -> bool:
    """Allow source omissions with the same article ambiguity as translation checks."""
    before, after = _comparison_quantities(source, text)
    return not (after - before)


def without_numeric_names(text: str, spans: list[str]) -> str | None:
    """Mask only exact nonempty, digit-free numeric-name spans verified by the task."""
    for span in spans:
        if not span.strip() or span not in text or re.search(r"\d", span):
            return None
    for span in sorted(set(spans), key=len, reverse=True):
        text = text.replace(span, " ")
    return text
