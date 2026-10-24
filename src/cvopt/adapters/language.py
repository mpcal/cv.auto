from lingua import Language, LanguageDetectorBuilder


class LinguaDetector:
    """English, German, French and Italian; "und" when undecided."""

    def __init__(self) -> None:
        self._detector = (
            LanguageDetectorBuilder.from_languages(
                Language.ENGLISH, Language.GERMAN, Language.FRENCH, Language.ITALIAN
            )
            .with_minimum_relative_distance(0.1)
            .build()
        )

    def detect(self, text: str) -> str:
        lang = self._detector.detect_language_of(text[:5000])
        return lang.iso_code_639_1.name.lower() if lang else "und"
