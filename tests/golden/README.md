# Golden data

Everything here is synthetic: invented people and companies.

- `src/*.typ` are the sources; `uv run python tests/golden/build.py` rebuilds the PDFs next to this file.
- `alice_cv.pdf`: Alice Example's English CV, three positions.
- `alice_reference_bergwerk.pdf`: a German reference letter (Arbeitszeugnis) from Bergwerk Software GmbH with
  one very good passage (the PostgreSQL migration, also in the CV), one good passage (churn models) and one
  coded-satisfactory passage (client presentations), which must yield a note, not evidence.
- `note.pdf`: a two-line PDF for the transcription smoke test.

The offline `tests/synthetic_golden.py` adapter is explicitly synthetic compatibility
code, not a provider capture. Full-profile position/award text combinations have no
historical pair recordings. For exact known fixture texts, and only complete known
scenario requirements, it supplies conservative `none` verdicts. Independent synthetic
profile-service tests exercise positive position/award coverage and dated-year behavior.
No such fallback is installed for adversarial tests or live recording runs.
