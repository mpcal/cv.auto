# cvopt — CV optimizer

cvopt builds an ideal target CV for one job description and explains the gaps against
your confirmed profile and recorded evidence. You feed it your documents (CV, reference letters, diplomas); it extracts
positions, records and claims, and you review every proposal before anything is stored.
For each job description it then writes a JSON Resume CV that maximizes an ATS-style
match score within two pages, an annotation file, a report, and PDFs.

Reports lead with two independent measures: the **target CV score** includes the ideal
CV's aspirational statements and placement; **profile-supported coverage** scores all
confirmed held claims with medium/high provability and stored fixed facts, without a
page limit or placement discount. A self declaration is not independent verification.
The signed target-minus-profile difference can be negative. Requirements list all
matching support and target statements separately from score-winning shares, with
original wording, current or stale sources, drop reasons, and static next actions.
Gap labels describe uncertainty in the recorded profile, not a conclusion about your
actual capability. Logistics remain separate and unscored.

Each generation pins its original facts, claims and evidence in `profile-snapshot.json`.
Stale and unconfirmed assertions can explain a gap but never add profile coverage.
Repeated quotations appear once in the annotated PDF evidence appendix; JSON preserves
full excerpts and their relationships. External CV reports and legacy runs without
matching provenance show profile coverage as unavailable, never as zero.

Generated statements are labelled honestly. Every statement is *unmodified*, *rephrased*,
*exaggerated* or *invented*, with a *provability* from `high` (backed by a reference letter,
diploma or named reference) down to `none`. Hard facts (employers, titles, dates, education,
languages, certificates, and every number and name in your claims) are never invented or
altered. The annotated PDF marks exaggerated (`E`, amber) and invented (`I`, red)
statements, so you decide what to keep before you send the clean version.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/). Everything else, including
the typesetter and fonts, comes as Python packages.

```sh
git clone <this repo> && cd cv.auto
uv sync
export ANTHROPIC_API_KEY=sk-ant-...      # the key is read only from the environment
export CVOPT_USER=you@example.com        # or pass --user to every command
uv run cvopt version
```

Your data lives under `~/.local/share/cvopt` (one shared SQLite database, plus one
directory per user named by a hash of the email, readable only by you). Text from your
documents leaves your machine only in calls to the configured model provider.

## Worked example: from your documents to an annotated PDF

**1. Ingest your CV.** cvopt reads PDF, DOCX, plain text and images (scans are transcribed
by the model), extracts proposals and matches them against what you already have.

```sh
uv run cvopt ingest ~/Documents/cv.pdf --kind cv
```

It then walks you through each proposal:

```
[a]ccept  [r]eject  [e]dit  [s]kip  [A]ccept all remaining  [q]uit
```

Rejecting a position also rejects the claims tied to it. `q` keeps your decisions;
resume with `cvopt review <batch>` (`cvopt review list` shows open batches). When nothing is
pending, confirm *Apply now?* to store the accepted items. `--accept-all` skips the prompts.

**2. Add evidence.** A reference letter raises the claims it confirms to provability
`high`. German Arbeitszeugnisse are read by their coded meaning: a passage graded only
"satisfactory" ("zu unserer vollen Zufriedenheit") becomes a note, not evidence.

```sh
uv run cvopt ingest ~/Documents/reference-acme.pdf --kind reference_letter
uv run cvopt claim list                     # what you have, with provability
uv run cvopt claim add --type skill --text "Apache Airflow" --keywords "Airflow" --learnable
uv run cvopt evidence add <claim-id> --kind reference_person --reference "Dr. Jane Doe"
```

Ids show as their last 8 characters; any unique suffix of at least 6 characters works.
`cvopt position|education|language|certificate|award list|add|edit|rm` and
`cvopt profile show|set` manage the rest by hand, e.g.
`cvopt profile set linkedin linkedin.com/in/you` (also `github`) or
`cvopt award add --title "Best Paper Award" --awarder CVPR --date 2020-06`. Awards appear in
their own section of every CV; a CV's dedicated awards section is extracted on ingestion.

**3. Take in a job description.** From a URL, a PDF or a text file:

```sh
uv run cvopt run new --url https://jobs.example.com/senior-data-scientist
# or: --pdf jd.pdf   or: --text jd.txt
```

This creates a run folder such as
`~/.local/share/cvopt/users/<key>/runs/2026-10-24T1622-acme-senior-data-scientist/` with
`jd.txt`, the original page, `requirements.json` and `run.json`. You may edit
`requirements.json` (an importance, a keyword) before generating. Pages that cannot be read
(errors, login walls) end with a message asking for `--pdf` or `--text`.

**4. Generate.**

```sh
uv run cvopt run generate 2026-10-24T1622     # a unique prefix of the folder name is enough
```

The first run makes many model calls (a few minutes); every answer is cached per user, so
regenerating or rescoring is fast and reproducible. The run folder then holds:

| File | Content |
| --- | --- |
| `cv.json` | The CV, valid JSON Resume, no annotations inside. |
| `annotations.json` | Per statement: fidelity, provability, source claims, requirements won, impact. |
| `report.json`, `report.csv` | Explained target/profile metrics, requirement coverage, selected/gap/dropped rows, and source provenance. |
| `cv-annotated.pdf` | The CV with `E`/`I` marks and row numbers, and the report as appendix. |
| `candidates.json` | Every candidate statement and why it was or was not chosen. |
| `profile-snapshot.json` | Original profile facts and claims, current/stale source links, and a material-content fingerprint. |

`cvopt run go --url …` does steps 3 and 4 in one go.

**5. Render the clean PDF to send**, after editing `cv.json` if you like:

```sh
uv run cvopt render <run-folder>/cv.json -o ~/cv-acme.pdf
uv run cvopt render <run-folder>/cv.json --annotated     # re-create the annotated PDF
```

The default PDF layout, `altacv-mod-1`, uses [AltaCV 1.7.0](https://typst.app/universe/package/altacv/)
(MIT), matching the single-column example in `typst-work`: Lato, teal section rules,
icon-led contacts and dates, and skill tags. The template, its Typst dependencies,
and all fonts are bundled for offline rendering without system fonts.
The selection applies to generation and standalone rendering, including annotated
PDFs. Clean exports contain no annotation marks or report appendix.

`altacv1` is accepted as an alias. The previous `atsthetic` and `classic` layouts
have been removed; configurations selecting them must use `altacv-mod-1` instead.

Education includes all original details by default: department, research group/adviser,
thesis topic, publications, awards, specializations, training, grade and links. These
are fixed facts; generation reserves space for them and never trims them for job
relevance. Set `[render] education_details = "compact"` to show only degrees,
institutions and dates. The setting applies to clean/annotated
PDFs; `cv.json` always retains the full education data.

CVs imported before education details were supported need a one-time refresh from
their stored source document (find the id with `cvopt document list`):

```sh
uv run cvopt education refresh --document <source-document-id>
# Review and apply the proposed details, then regenerate the run:
uv run cvopt run generate <run-folder-or-prefix>
```

Refresh fills missing descriptions and grades and appends missing bullets/links,
without overwriting confirmed values or creating duplicate degrees. Use
`--accept-all` to accept and apply the proposals without prompts. Manual entry is
also available with `education add|edit --summary "…" --highlight "…"` (repeat
`--highlight` for each bullet; editing replaces the list). If all fixed facts cannot
fit the configured page budget, increase `[optimizer] page_budget`; education will
not be silently removed.

Use `--link "Research demo=https://example.org/demo"` to add a named education link
(repeat for multiple links); `education edit --link` replaces the links and
`education edit --clear-links` removes them. In the review editor, lists are shown
as JSON so punctuation and links survive edits.

**6. Score an edited CV** against the same requirements (the run's date is reused):

```sh
uv run cvopt score <run-folder>/cv.json --requirements <run-folder>/requirements.json \
    --annotations <run-folder>/annotations.json --report
```

**7. Your data.** `cvopt export -o backup.zip` writes all your records, documents and runs
into one archive; `cvopt delete-user --confirm you@example.com` deletes all of them.

## How the score works

Each requirement of the job description has a weight (must = 3, nice = 1) and is covered by
the CV element that matches it best: exact keyword 1.0, synonym 0.9, same meaning 0.6,
related 0.3, discounted by where the element sits (recent highlight 1.0, older highlight
0.85, headline or summary 0.8, skills list 0.7). The score is the weighted coverage, 0–100;
each statement's impact is the points it wins, and the report lists the missing points per
requirement as gaps. It approximates keyword-and-meaning ATS matchers; it is not any
vendor's score.

## Find runs and inspect usage

```sh
uv run cvopt run list
uv run cvopt run show <run-folder-or-prefix>
uv run cvopt run list --json
uv run cvopt run show <run-folder-or-prefix> --json --out run-details.json
```

Discovery uses only the selected user's local run directory and needs no API key. It
shows publication and last-attempt status/dates, saved target and optional full-profile
coverage scores, usage, and available artifact paths, including both `cv.pdf` and
`cv-annotated.pdf`. Older runs remain readable; missing scores, usage and artifacts
stay unavailable. A failed regeneration leaves the prior complete publication selected.
A post-publication diagnostic error is shown separately as `published_with_error`;
a selected publication remains usable when attempt diagnostics could not be updated.

Generation reports stage durations and task/cache counters on terminal stderr. Progress
is silent off-terminal; JSON stdout contains one complete value even with `--out`.
Observers are optional local callbacks; no telemetry is transmitted.

Published `usage.json` records new billing for that generation operation, including
profile coverage, context matching and target scoring calls. `intake-usage.json` records
the JD's original intake separately; `usage.json` retains that contribution without
billing it again on regeneration. Failed generation summaries are in `last-attempt.json`.
Application cache hits incur no new model billing, so a cache-only operation has known
zero new usage/cost even without rates. Historical cache creation cost remains unknown.
Provider cache reads and writes are separate billable token categories.

`known_subtotal_usd` includes measured tokens with configured prices. `total_usd` stays
null unless all usage and needed rates are known; no model prices are assumed. A missing
token field differs from measured zero. SDK retries use the actual raw-response retry
count: final response tokens remain a known subtotal, and hidden earlier-attempt usage
makes the total incomplete. Provider failures without observable usage remain incomplete.
These values describe configured token costs, not an invoice reconciliation.

## Compare roles against your current profile

```sh
uv run cvopt run compare <run-one> <run-two> [more-runs...]
uv run cvopt run compare <run-one> <run-two> --as-of 2026-06-30 --json
uv run cvopt run compare <run-one> <run-two> --out comparison-export
```

Comparison reads each run's **edited** `requirements.json` and evaluates all roles against
one current profile snapshot, one date (today by default), and the current scoring settings.
It ranks profile-supported coverage descending, must-requirement weighted missing points
ascending, then stable run ID ascending. Complete confirmed medium/high held claims and
stored fixed facts contribute at neutral placement without a page budget. Overlapping
experience periods count once. Coverage measures recorded support, not actual capability.
Comparison can need model judgments or verified translations; cached/no-model cases need
no API key. An unverifiable confirmed translation aborts comparison.

Saved aspirational target scores retain their generation dates and remain separate from
this ranking. Profile fingerprint, edited requirements, effective generation configuration,
and date are checked for staleness independently; missing legacy metadata is `null`/unknown.
Scoring, optimizer, layout/render, model/provider and prompt identity affect configuration
staleness. Prices, logging and storage paths do not. Comparison never regenerates a target.

Recurring gaps require the same complete normalized requirement signature in at least two
distinct selected roles. Identity includes text, category, keywords/synonyms and all typed
threshold/qualification fields; IDs and importance are excluded. Overlapping keywords alone
are insufficient. Duplicate rows retain their IDs and weighted missing points but do not
create additional roles. Priority is summed weighted missing percentage points across role
instances: each role's scored requirements normalize to 100 points. These are support gaps,
with no learning-time estimates or guided workflow.

Only `--out DIR` writes `comparison.json` and `comparison.csv`; JSON includes the shared
snapshot, role support/provenance, saved-score staleness and new comparison operation usage.
CSV contains role rankings, per-role requirement coverage/gaps and recurring-gap instances.
Comparison usage spans all roles and remains separate from saved JD intake/generation costs.

Fixed-record document links become stale after material edits. Explicitly reconfirm the
current record against the old owned document with:

```sh
uv run cvopt evidence reconfirm <fact-source-id> --fact-source
uv run cvopt evidence reconfirm <fact-source-id> --fact-source --excerpt 'exact source quote'
```

The command revalidates the current record/source, retains the stale attachment, and creates
a fresh valid link. Reverting a record alone does not reactivate its old links. Without
`--fact-source`, the command retains its existing claim-evidence behavior. Re-ingesting an
identical document remains a duplicate skip.

## Configuration

Optional: `~/.config/cvopt/config.toml` (or `--config FILE`, or `CVOPT_CONFIG`). Unknown
keys are rejected. The defaults:

```toml
[app]
data_root = "~/.local/share/cvopt"
default_user = ""                 # --user and CVOPT_USER take precedence

[llm]
strong_model = "claude-sonnet-5-5"
fast_model = "claude-haiku-5-5"
max_concurrency = 4

[optimizer]
page_budget = 2
exaggeration_min_gain_pp = 1.0    # an exaggeration must beat its plain version by this

[scoring]
weight_must = 3
weight_nice = 1
recency_years = 5

[render]
template = "altacv-mod-1"        # default; "altacv1" is an alias
education_details = "all"        # or "compact" for degrees, institutions and dates only
font = "Lato"
font_size_pt = 10
# margin_mm = 18                # optional uniform override; defaults: x=9, y=15 mm
```

Optional per-model prices use the exact returned model ID and USD per million tokens.
All rates must be finite and nonnegative. Omit unknown rates; zero means an explicitly
configured zero rate. For example, this is a **synthetic arithmetic example**, not a
provider price list:

```toml
[llm.prices."example-model"]
input = 2.0
output = 4.0
cache_read = 0.5
cache_write = 1.0
```

See `src/cvopt/config.py` for every setting. Exit codes: 0 success, 1 input error,
2 model failure, 3 the rules or the page budget cannot be met, 4 internal error (details in
`<data_root>/cvopt.log`, which never contains your documents' text).

## Development

```sh
uv run pytest                    # offline: model answers come from tests/recordings
uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports
```

- `uv run pytest -m live` runs the golden scenarios against the real model
  (needs `ANTHROPIC_API_KEY`). `CVOPT_RECORD=1` adds new answers to the recordings;
  `CVOPT_RECORD=fresh` re-records all of them. A prompt-version bump makes the affected
  offline tests fail with `MissingRecording` until re-recorded.
- `tests/golden/` holds synthetic test data (invented people and companies);
  `uv run python tests/golden/build.py` rebuilds its PDFs from the Typst sources.
- `uv run python scripts/calibrate_layout.py` re-measures the `[layout]` constants after a
  template change.
- Schema changes: edit `src/cvopt/adapters/db/schema.py`, then
  `uv run alembic revision --autogenerate -m "..."`; migrations run automatically at startup.
  Migrations `0004` and `0005` are forward-only: the old schemas cannot represent retained
  assertion and review history, so Alembic refuses a downgrade before changing the database.
  Rolling back the application requires a separately managed pre-upgrade database copy; the application
  does not create backup copies automatically.
  Pending proposals from before `0005` have unknown original assertions and require renewed
  review before attaching source support. Bulk acceptance cannot resolve stale-target conflicts.
- The design documents are in `masterplan/`; current service and isolation contracts are in
  [`docs/development.md`](docs/development.md).

Fonts: Lato, © Łukasz Dziedzic; Font Awesome Free 7.1.0, © Fonticons; and
Source Sans 3, © Adobe (available as an explicit override). All use the SIL Open
Font License; licenses and attribution are bundled beside the font files.
See `src/cvopt/resources/packages/README.md` for the pinned Typst dependencies
and the small AltaCV adaptations.
