# CV Optimizer — Phase 2: Software Architecture

Oct 17, 2026 · @Michael

The app is a layered Python application: a CLI calls one async service layer, which runs pure domain logic and LLM tasks, and reaches storage, the LLM, document extraction, the web and the typesetter only through ports. All users share one database, partitioned by user id; each user's files live in their own directory. This document builds on the Phase 1 functional specification; section numbers like "P1 §8" refer to it.

## 1. At a glance

&#91;embedded content: architecture · 4 layers, 24 parts\]

Calls flow strictly downward: the CLI calls a service, the service uses the core, and the core reaches database, files, model, web and typesetter only through ports. Later interfaces and adapters plug in without changes to the core.

## 2. Architecture decisions

Ten decisions shape everything below; most exist so that the later web API and MCP server can be added without touching the core.

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Ports-and-adapters layering: interfaces → services → core → ports, with adapters behind the ports. | The CLI, a later web API and a later MCP server all call the same services; storage, LLM and typesetter can be swapped. |
| D2 | One service per phase-1 function (F1–F8). Every service operation takes a `UserContext` (the user id) as its first argument. | Maps the spec 1:1 to code; per-user isolation is enforced at the API boundary. |
| D3 | The service layer is async. | The later web API and MCP server are async; LLM calls run concurrently today. |
| D4 | One shared database holds all users' records and the LLM cache; each user's files (source documents, run folders) live in a per-user directory. | One schema serves the CLI now and a multi-user server later; per-user directories keep files easy to export and delete. |
| D5 | Every record carries `user_id`. | Repositories bound to one user add it to every query, and composite foreign keys stop a record from pointing at another user's record. |
| D6 | All language-model work goes through named, versioned LLM tasks with typed input and output schemas. | Prompts stay testable and replaceable; outputs are validated, never parsed ad hoc. |
| D7 | Every LLM task result is cached per user, keyed by task, prompt version, model and input. | Makes scoring and generation reproducible (P1 §8.6) and makes tests run without a live model. |
| D8 | Scoring is a pure function of CV elements, requirements, match judgements, configuration and an as-of date. | One scorer serves the optimizer, standalone scoring (F5) and tests; results are explainable. |
| D9 | The optimizer is greedy over pre-generated candidate statements, with the renderer as final judge of length. | Simple to implement and reason about; weighted max-coverage under a budget is handled well by greedy selection. |
| D10 | A deterministic validator checks every hard rule of P1 §6–7 before outputs are written; tests reuse it. | Catches model mistakes (altered figures, wrong fidelity) regardless of prompt quality. |

## 3. Components

Twenty components across the interface, service, core and port/adapter layers, plus configuration. Dependencies point downward only: no core component imports a service, no service imports the CLI, and only adapters import third-party I/O libraries.

| Component | Layer | Responsibility | Depends on |
| --- | --- | --- | --- |
| CLI | Interface | Parses commands, resolves the user id, calls services, prints results, runs the interactive review. No business logic. | all services |
| IngestionService (F1) | Service | Stores a document, extracts text, runs extraction tasks, matches against existing data, creates a review batch; records item decisions and applies accepted items. | Repository, FileStore, TextExtractor, TaskRunner, Rules |
| DataService (F2) | Service | Creates, reads, updates and deletes profile, fact records, claims and evidence; recomputes provability. | Repository, Rules |
| JdService (F3) | Service | Fetches or reads the JD, runs `parse_jd`, creates the run folder with `jd.txt`, `requirements.json`, `run.json`. | WebFetcher, TextExtractor, TaskRunner, FileStore, Clock |
| GenerationService (F4) | Service | Runs the generation pipeline (section 6) and writes all run outputs. | Optimizer, Judge, TaskRunner, Validator, ScoringEngine, ReportService, RenderService, Repository, FileStore |
| ScoringService (F5) | Service | Turns a JSONResume file into CV elements, obtains judgements, calls the scoring engine. | Judge, ScoringEngine, FileStore |
| ReportService (F6) | Service | Builds report rows and summary figures; writes `report.json` and `report.csv`. | ScoringEngine output, FileStore |
| RenderService (F7) | Service | Produces annotated or clean PDFs. | Renderer, FileStore |
| AccountService (F8) | Service | Exports a user's records and files as one archive; deletes both after confirmation. | Repository, FileStore |
| Domain model | Core | Typed models: entities, requirement, CV element, candidate, statement, annotation, report row, JSONResume subset. | — |
| Rules | Core | Provability derivation (P1 §4), fidelity-provability matrix (P1 §7), position ordering, CEFR and degree comparison, hard-fact checks. | Domain model |
| ScoringEngine | Core | Pure scoring and impact attribution (P1 §8). | Domain model |
| Optimizer | Core | Greedy candidate selection under the line budget (section 6). | ScoringEngine, Rules |
| Validator | Core | Checks every hard rule; returns violations per statement. | Rules |
| LLM task definitions | Core | One definition per task: prompt template, version, input and output schemas (section 5). | Domain model |
| TaskRunner | Core | Executes a task: cache lookup, model call, schema validation, retry, cache write. | LLMClient, Repository (cache) |
| Judge | Core | Returns the match level for requirement-element pairs: keyword pass first, then the `judge_match` task. | TaskRunner, Rules |
| Ports | Port | Repository, FileStore, LLMClient, TextExtractor, WebFetcher, Renderer, Clock: abstract interfaces owned by the core. | — |
| Adapters | Adapter | Shared-database repository (SQLite in v1), local file store, LLM provider client, PDF/DOCX/OCR extractor, HTTP fetcher, typesetting renderer, system clock. Chosen in phase 3. | third-party libraries |
| Config | Cross-cutting | Loads tunables (P1 §11.3) and system settings; snapshots them into each run. | — |

## 4. Storage

All users share one database for their records and the LLM cache; each user's files live in a per-user directory. Repositories are bound to one user, so no code path can read another user's records. Deleting a user removes their rows in one transaction and their directory; exporting writes both into one archive.

### 4.1 Layout

The directory name is the user key: the first 16 hex characters of the SHA-256 of the normalized email, so paths carry no personal data.

```
<data_root>/
  cvopt.sqlite                  shared database (v1: SQLite)
  users/
    <user-key>/
      documents/
        <document-id>.<ext>     original source files
      runs/
        <run-folder>/           P1 §9.1 files, incl. run.json and candidates.json
```

`run.json` holds the run id, created\_at, as-of date, JD source, language, model ids, prompt versions and a configuration snapshot. `candidates.json` holds every candidate statement with its judgements and why it was or was not selected.

### 4.2 Isolation

- Repositories are constructed for one `UserContext`. Every query filters on its `user_id` and every insert sets it; no repository method takes a user id as a parameter.
- Foreign keys are composite with `user_id` — e.g. claim (user\_id, position\_id) → position (user\_id, id) — so the schema itself forbids links between users.
- Uniqueness is per user, e.g. (user\_id, sha256) on `source_document`.
- The schema stays portable to Postgres: no SQLite-only types or functions; JSON is stored as text.

### 4.3 Logical schema

Ids are ULIDs (sortable strings). Months are `YYYY-MM`; timestamps are ISO 8601 in UTC. Every table except `schema_meta` carries `user_id`; record tables also carry `created_at` and `updated_at`.

| Table | Columns | Notes |
| --- | --- | --- |
| users | user\_id (key; normalized email), user\_key, created\_at | One row per user, created on first use. |
| profile | user\_id (key), name, email, phone, city, country, website, profiles (JSON list of network and url) | One row per user. |
| position | id, employer, employer\_location, title, start\_month, end\_month (null = current) | Deleting a position that still has claims is refused; claims must be moved or deleted first. |
| education | id, institution, area, degree\_type (as written), degree\_level, start\_month, end\_month, grade | degree\_level: `secondary`, `vocational`, `bachelor`, `master`, `doctorate`, `other`; set at ingestion for matching (P1 §8.2). |
| language | id, language\_code (ISO 639-1), level (`A1`–`C2`, `native`) | Codes make "Deutsch" and "German" the same language. |
| certificate | id, name, issuer, issued\_month, url |  |
| fact\_source | record\_type, record\_id, document\_id | Links fact records to source documents; drives their provability. |
| claim | id, type, text, text\_lang, position\_id (nullable), keywords (JSON list), status (`confirmed`, `proposed`), origin, provability | provability is stored but always recomputed by Rules when evidence changes. |
| evidence | id, claim\_id (cascade delete), kind, document\_id (nullable), excerpt, reference\_name, reference\_contact |  |
| source\_document | id, kind, filename, sha256 (unique per user), mime\_type, language, issuer, position\_id (nullable), extracted\_text, received\_at | Deleting it cascades to its evidence and fact\_source rows; claims left without evidence become `proposed`. |
| review\_batch | id, document\_id, status (`open`, `applied`, `discarded`), applied\_at | One batch per ingested document (section 8). |
| proposal\_item | id, batch\_id, seq, action, target\_id (nullable), payload (JSON), decision (`pending`, `accept`, `reject`), note | Decisions are saved one at a time during the interactive review. |
| llm\_cache | key, task, prompt\_version, model, response (JSON), created\_at | Unique per user and key; never shared between users (5.3). |
| schema\_meta | version | Global; for migrations. |

## 5. LLM integration

The language model is used only through thirteen named tasks, each with a versioned prompt and typed input and output. Results are validated against the output schema and cached per user, so a repeated call never reaches the model.

### 5.1 Task contract

Every task definition has: a name, a `prompt_version` string, an input model, an output model, a prompt template, and a model tier (`strong` or `fast`, mapped to model ids in configuration). TaskRunner executes it:

1. Canonicalize the input (sorted keys, normalized whitespace) and compute the cache key (5.3). On a hit, return the cached output.
2. Call the model, requesting output that conforms to the output schema. No sampling parameters are set.
3. Validate the response against the output model. On failure, retry up to 2 times, appending the validation error to the prompt; then raise `LLMTaskError`.
4. Store the validated output in the cache and return it.

### 5.2 Task catalogue

| Task | Input | Output | Used by | Tier |
| --- | --- | --- | --- | --- |
| `transcribe_document` | scanned PDF or image | verbatim text in reading order | F1, F3 | fast |
| `extract_cv` | CV text | profile fields, positions, education (with degree level), languages (codes, levels), certificates, claims (type, text, keywords, position reference) | F1 | strong |
| `extract_reference_letter` | letter text, existing positions | issuer, author, period, matched or new position; passages with excerpt, coded grade (`very_good`, `good`, `satisfactory`, `poor`, `none`) and the claims each supports | F1 | strong |
| `extract_certificate` | document text | education or certificate record, attested skill claims | F1 | strong |
| `match_claims` | one new claim, existing claims of the same position | per pair: same or different, plus a quantity-conflict flag | F1 | fast |
| `parse_jd` | JD text | title, company, language, requirements with structured fields (years, language code and level, degree level and fields, certificate names), logistics items | F3 | strong |
| `judge_match` | one requirement, up to 40 element texts | per element: `semantic`, `partial` or `none`, with a one-line reason | F4, F5 | fast |
| `translate` | texts, target language | texts | F4 | fast |
| `generate_variants` | 1–3 claims of one position, the requirements they serve, JD terms | rephrased and exaggerated variants, each with intended fidelity | F4 | strong |
| `generate_inventions` | one uncovered requirement, positions, confirmed claims | candidates with slot (skill or highlight), position, text, inferable flag, inferred-from claim ids | F4 | strong |
| `classify_fidelity` | candidate text, source claim texts | fidelity with a one-line reason | F4 | strong |
| `write_summary` | selected statements, requirements, JD title | 3 headline and up to 6 summary-sentence candidates, each with source statements and intended fidelity | F4 | strong |
| `group_skills` | selected skill keywords, language | skill groups (name, keywords) | F4 | fast |

The model never assigns `exact` or `synonym`: those come from the requirement's keyword lists, and `judge_match` only confirms that the meaning fits (section 7.2).

### 5.3 Cache

- Key: SHA-256 of the canonical JSON of task name, prompt version, model id and input. Entries never expire; bumping a prompt version invalidates its entries.
- `judge_match` is cached per pair, keyed by requirement text and element text, so the Judge can batch freely and only sends pairs it has not seen.
- Cache rows carry the user id and are never shared between users; deleting a user deletes them.

### 5.4 Determinism

No sampling parameters are set, and providers are not bit-exact anyway, so reproducibility comes from the cache, not the model. Inputs are canonicalized and processed in sorted order; no step uses randomness. Each run records its model ids and prompt versions in `run.json`.

### 5.5 Untrusted text

JD text, web pages and documents are data, never instructions. Prompts wrap them in clearly delimited blocks and tell the model to ignore instructions inside them; the output schema limits what any response can do.

## 6. Generation pipeline

Generation builds a pool of candidate statements, discards any that fail verification, selects greedily within a line budget, then loops through the renderer until the CV fits its pages.

&#91;embedded content: generation pipeline · 11 steps, 1 loop\]

A statement that breaks a rule, or the lowest-impact statement when the PDF runs over, is dropped and the CV reassembled; after 20 rounds the run fails.

### 6.1 Steps

1. **Load** confirmed claims, fact records, profile, `requirements.json` and configuration; take the as-of date from `run.json`.
2. **Translate** claims whose language differs from the JD's (`translate`). A faithful translation keeps fidelity `unmodified`.
3. **Judge relevance** of every requirement-claim pair (section 7). Claims matching no requirement at `partial` or better leave the pool; they remain available as filler (P1 §6.2). Requirements on fact records (education, language, certification, years) are evaluated here as well.
4. **Generate candidates.** Each candidate has: id, slot (highlight of a position, skill keyword, headline, summary sentence), text, source claim ids, intended fidelity, inferred-from claim ids, assertion key (invented candidates only) and estimated lines.
   - Every relevant claim yields its unmodified text, plus `generate_variants` output (rephrased, exaggerated, and merges of 1–3 claims of the same position).
   - Skill claims also yield skill-keyword candidates, spelled as in the JD where a synonym matches.
   - Every requirement whose best sourced coverage is below 1, and whose category allows invention (P1 §8.1), gets `generate_inventions` output.
5. **Verify** every candidate:
   - `classify_fidelity` checks every candidate that is not unmodified. The final fidelity is the more altered of intended and classified (unmodified < rephrased < exaggerated < invented).
   - Deterministic hard-fact check: every number, date and proper name in a sourced candidate must occur in its sources; invented candidates contain no digits. Failures are discarded.
   - Rules assign provability (P1 §7).
6. **Judge candidates** against every requirement (section 7).
7. **Select** greedily within the line budget (6.2).
8. **Write headline and summary** with `write_summary` from the selection. These candidates are verified and judged like the rest; the best headline and 2–4 summary sentences (at least 2, even at zero gain) are chosen. Their lines are reserved in the budget from the start.
9. **Assemble and score:** group skill keywords (`group_skills`), order positions (P1 §6.2), build `cv.json` and the annotations, and score with the ScoringEngine — the same code F5 uses.
10. **Validate and render.** The Validator checks every rule; a failing statement is dropped and the pipeline returns to step 9 (a failing fact record is a bug and aborts the run). The Renderer renders the clean CV to a temporary file and reports its page count; the annotated PDF is rendered once, in step 11. Over budget, the lowest-impact statement is dropped and the pipeline returns to step 9. After 20 iterations the run fails with a message.
11. **Write** `cv.json`, `annotations.json`, `report.json`, `report.csv`, `cv-annotated.pdf` and `candidates.json`, and update `run.json`.

### 6.2 Selection algorithm

The score takes each requirement's best element, so it is a weighted max-coverage objective; greedy selection by gain per line is the standard approach and easy to verify.

**Line budget.** B = pages × `lines_per_page` − fixed lines (contact header, section headings, one title line per position, all education, language and certificate lines, and the reserved headline and summary lines). A statement costs ⌈characters ÷ `chars_per_line`⌉ lines; a skill keyword costs its characters ÷ `chars_per_line`, since keywords share lines. Both constants are calibrated to the template in phase 3.

**Loop:**

1. For each eligible candidate c, compute its gain Δ(c) = S(selected + c) − S(selected).
2. A candidate is eligible when it fits the remaining budget; none of its source claims already backs a selected highlight (skill keywords are counted separately); its assertion key is unused; and, if exaggerated, its gain beats the best non-exaggerated candidate of the same source claims by at least 1.0 pp.
3. Select the candidate with the highest Δ ÷ lines. Gains within 0.1 pp count as ties, broken by less altered fidelity, then higher provability, then shorter text, then candidate id.
4. Stop when no eligible candidate has Δ > 0.

**Afterwards:**

- **Filler:** positions without highlights each receive their most relevant sourced, unmodified or rephrased candidate if it fits, most recent position first.
- **Invented skills:** the skill-keyword and highlight candidates of one invented skill share an assertion key, so only the higher-scoring placement survives. This settles the ambiguity in P1 §6.6.

## 7. Scoring engine and judge

Scoring has two parts: the Judge decides a match level for every requirement-element pair, and the ScoringEngine turns those levels into the score and its attribution with no further model calls. Both work on any JSONResume file, so F5 can score a CV the user has edited.

### 7.1 CV elements

| Element | Source in `cv.json` | Placement factor (P1 §8.3) |
| --- | --- | --- |
| Headline | `basics.label` | 0.8 |
| Summary sentence | `basics.summary`, split into sentences | 0.8 |
| Highlight | `work[i].highlights[j]` | 1.0 if the position is current or ended less than 5 years before the as-of date, else 0.85 |
| Skill keyword | `skills[i].keywords[j]` | 0.7 |
| Education | `education[i]` | 1.0 |
| Language | `languages[i]` | 1.0 |
| Certificate | `certificates[i]` | 1.0 |

- Each element's id is its JSON pointer, plus a sentence index for summary sentences.
- Sentences split deterministically: at `.`, `!` or `?` followed by a space and an uppercase letter, ignoring a configured list of abbreviations. Generation joins sentences with single spaces, so indexes survive a round trip.
- JSONResume dates may be `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; all are read as months.
- Annotations attach to elements by pointer and text. An element whose text no longer matches its annotation is reported with blank fidelity and provability.

### 7.2 Judge

For text categories (hard skill, soft skill, experience, domain):

1. Keyword pass: an element hits `exact` if it contains a JD keyword, `synonym` if it contains a listed synonym — whole words or phrases, case-insensitive, after Unicode normalization.
2. Every pair also goes to `judge_match`, batched per requirement and cached per pair (P1 §8.2 demands that the meaning fits).
3. Level: a keyword hit counts only if `judge_match` did not answer `none`; otherwise the `judge_match` level stands.

For structured categories the comparison is deterministic (P1 §8.2):

- **Language:** names map to ISO 639-1 codes and fluency texts to CEFR levels through configured tables (English and German names).
- **Education:** degree names (BSc, Master, Dr., …) map to degree levels through a configured table; the field comparison uses `judge_match`.
- **Certification:** normalized name equality; otherwise `judge_match` decides equivalence.
- **Years of experience:** covered years are the union of the date ranges of positions holding a highlight judged `semantic` or better for that requirement. No placement factor applies.

### 7.3 Score and attribution

The ScoringEngine implements P1 §8.4–8.5 exactly. For years-of-experience requirements, credit is collective: each month of the union goes to the covering position with the latest start, positions share the points by assigned months, and within a position the points go to its best-matching highlight.

Its result holds: the total score; per requirement the weight, coverage, winning elements and gap points; per element the impact. Impacts plus gap points sum to 100.

### 7.4 As-of date

Recency and current positions depend on a date. It comes from `run.json` when the CV sits in a run folder, else from an explicit option, else from the Clock. Rescoring a run therefore uses the run's own date.

## 8. Ingestion and review

Ingesting a document never changes the user's data directly. It produces a review batch of proposal items; the user walks through the items one by one at terminal prompts, and the accepted items are applied in one transaction.

### 8.1 Flow

1. **Store:** hash the file; a known hash ends with a message. Save it as `documents/<id>.<ext>`, extract text (OCR when needed), detect the language, insert the `source_document` row.
2. **Extract** with the task for the document kind (section 5.2).
3. **Match** against existing data:
   - Positions: normalized employer names with similarity at or above a configured threshold, and overlapping dates.
   - Claims: `match_claims` against existing claims of the same position, pre-filtered by keyword overlap.
   - Differing dates, titles or quantities become `conflict` items.
4. **Create** a `review_batch` with its proposal items, all `pending`; the batch status is `open`.
5. **Review:** the user walks through the pending items one by one at terminal prompts (8.3). Each decision is saved at once, so a review can stop and resume later.
6. **Apply:** once no item is pending and the user confirms, every accepted payload is re-validated against its action's schema, and the accepted items are applied in `seq` order in one transaction. Afterwards, provability is recomputed for affected claims and the batch becomes `applied`.

### 8.2 Proposal actions

| Action | Payload | Effect when accepted |
| --- | --- | --- |
| `set_profile_field` | field, value | Sets an empty profile field; a filled one yields a conflict item instead. |
| `create_position` | position fields | Inserts the position. |
| `update_position` | position id, missing fields | Fills empty fields only. |
| `create_education`, `create_language`, `create_certificate` | record fields | Inserts the record and its `fact_source` link. |
| `link_fact_source` | record type and id | Adds a `fact_source` link to an existing record. |
| `create_claim` | type, text, language, position, keywords, evidence | Inserts a `confirmed` claim with its evidence. |
| `add_evidence` | claim id, evidence | Adds evidence to an existing claim. |
| `conflict` | record or claim, field, stored value, document value | None; the user resolves it with F2. |
| `note` | text | None; e.g. a reference-letter passage that reads as coded criticism. |

Items may reference other items of the same batch as `$item:<seq>`, e.g. a claim tied to a position proposed in the same batch. Rejecting an item rejects the items that depend on it, unless the user re-points them.

### 8.3 Interactive review

The CLI runs the review; the service layer offers only item-level operations (9.1), so a later web UI can show the same items on a screen.

For each pending item, in `seq` order, the CLI shows its place ("item 3 of 12"), the action and its payload fields. Claims also show the evidence excerpt and source document; conflicts show the stored and the document value. It then asks:

```
[a]ccept  [r]eject  [e]dit  [s]kip  [A]ccept all remaining  [q]uit
```

- **Accept** or **reject** saves the decision. Rejecting also rejects the items that depend on it and lists them.
- **Edit** asks for each field in turn, showing the current value; Enter keeps it. Lists are typed comma-separated; a position field takes an existing position id or `$item:<seq>`. Each value is validated at once and asked again if invalid. The edited item is then accepted.
- **Skip** leaves the item pending; skipped items come round again after the others.
- **Accept all remaining** asks for one confirmation, then accepts every pending item.
- **Quit** stops; decisions so far are kept, and `cvopt review BATCH` resumes.
- For `conflict` and `note` items, accept means acknowledged; they never change data.

When nothing is pending, the CLI shows how many items were accepted and rejected and asks whether to apply; answering no leaves the batch open. `cvopt ingest` offers to start the review right away. For scripts and tests, `--accept-all` skips the prompts.

## 9. Service operations and CLI

The service operations below are the system's only public surface: the CLI wraps them now, and the web API and MCP server will wrap the same list later. Every operation takes a `UserContext` first; it is omitted from the table.

### 9.1 Service operations

| Service | Operation | Inputs | Returns |
| --- | --- | --- | --- |
| Ingestion | `ingest_document` | file path, document kind | batch id, item counts |
| Ingestion | `list_items` | batch id | proposal items with their decisions |
| Ingestion | `decide_item` | batch id, seq, decision, edited payload (optional) | the item, plus any dependents rejected with it |
| Ingestion | `apply_batch` | batch id | counts applied and rejected |
| Ingestion | `list_batches`, `discard_batch` | status filter / batch id | batches / — |
| Data | `get_profile`, `update_profile` | — / fields | profile |
| Data | list, get, create, update, delete for positions, education, languages, certificates | record id, fields | records |
| Data | `list_claims` | filters: type, position, provability, status | claims with evidence |
| Data | `create_claim`, `update_claim`, `delete_claim` | type, text, position, keywords, evidence kind (`self_declared` or `learnable_declared`) | claim |
| Data | `add_evidence`, `remove_evidence` | claim id, kind, document id, excerpt, reference person / evidence id | claim with new provability |
| Data | `list_documents`, `delete_document` | — / document id | documents / affected claims |
| JD | `create_run` | URL, PDF path or text | run id, requirement summary |
| Generation | `generate` | run id | output paths, score |
| Scoring | `score` | CV path, requirements path, annotations path (optional), as-of date (optional) | score result |
| Report | `build_report` | score result, annotations, dropped claims (optional) | report paths |
| Render | `render` | CV path, mode (`annotated`, `clean`), annotations and report (annotated only), output path | PDF path |
| Account | `export_user` | output path | archive path |
| Account | `delete_user` | confirmation (the user's email typed again) | — |

### 9.2 CLI

The CLI is named `cvopt`. The user id comes from `--user`, else the environment variable `CVOPT_USER`, else the configuration file; the data root from `--data-root` or configuration. Commands map 1:1 to operations; a run is addressed by its folder name or a unique prefix of it.

```
cvopt ingest FILE --kind cv|reference_letter|diploma|certificate|other [--accept-all]
cvopt review list | BATCH [--accept-all] | discard BATCH
cvopt profile show | set FIELD VALUE
cvopt position|education|language|certificate list | add ... | edit ID ... | rm ID
cvopt claim list [--type T] [--position ID] [--provability P] [--status S]
cvopt claim add --type skill|experience|achievement --text TEXT [--position ID] [--learnable]
cvopt claim edit ID ... | rm ID
cvopt evidence add CLAIM --kind KIND [--document ID] [--excerpt TEXT] [--reference NAME] | rm ID
cvopt document list | rm ID
cvopt run new --url URL | --pdf FILE | --text FILE
cvopt run generate RUN
cvopt run go --url URL | --pdf FILE | --text FILE        (new + generate)
cvopt score CV.json --requirements FILE [--annotations FILE] [--as-of YYYY-MM-DD] [--report]
cvopt render CV.json [--annotated --annotations FILE --report FILE] [-o OUT.pdf]
cvopt export [-o ARCHIVE.zip]
cvopt delete-user --confirm EMAIL
```

Exit codes: 0 success, 1 input error, 2 LLM failure, 3 validation failure, 4 internal error.

## 10. Cross-cutting concerns

Configuration, errors, logging, concurrency and the future adapters each have one fixed rule set, so no component invents its own.

### 10.1 Configuration

- One configuration file holds: data root, database location, default user, LLM provider and model id per tier, maximum concurrency, the tunables of P1 §11.3, layout constants (`lines_per_page`, `chars_per_line`), language and degree mapping tables, the abbreviation list for sentence splitting, and matching thresholds.
- Secrets (the LLM API key) come only from environment variables, never from files.
- Every run stores a snapshot of the effective configuration in `run.json`.

### 10.2 Errors

Services raise only these errors; each interface translates them (the CLI to exit codes, a later web API to HTTP status codes).

| Error | Raised when | CLI exit code |
| --- | --- | --- |
| `InputError` | unreadable file, failed URL fetch, malformed review file, unknown run | 1 |
| `NotFoundError`, `ConflictError` | missing record; refused change, e.g. deleting a position that has claims | 1 |
| `LLMTaskError` | schema validation or provider failure after retries | 2 |
| `ValidationFailed` | generation cannot satisfy the rules or the page budget | 3 |
| any other exception | bug | 4 |

All inherit from `CvoptError`.

### 10.3 Logging and privacy

- Logs are structured (one JSON object per line) and carry ids, counts, durations, task names and cache hits — never claim text, document text or prompts. Text appears only in an explicitly enabled debug mode.
- Personal data leaves the machine only in calls to the configured LLM provider. The JD fetch carries no user data.
- Files and directories are created readable by the owning OS user only; paths contain the user key, not the email.

### 10.4 Concurrency

- LLM calls run concurrently up to the configured maximum (default 4). The LLM adapter retries rate-limit errors with exponential backoff, up to 5 times.
- The shared SQLite file runs in WAL mode with a busy timeout, and write transactions stay short. A lock file in each user directory stops two processes from writing one user's data at once.
- Pipeline steps run in order; independent LLM calls within a step run in parallel.

### 10.5 Future adapters

Not built in v1, but the design must not block them:

- **Web API:** routes map 1:1 to the operations of 9.1. Authentication middleware produces the `UserContext`. Ingestion and generation become background jobs with status polling.
- **MCP server:** tools map 1:1 to a subset of operations (list claims, create run, generate, score, render), with the `UserContext` taken from the session's authentication.
- **Web UI:** talks only to the web API; its review screen edits the same proposal items as the terminal review.
- **Server database:** a Postgres repository adapter replaces SQLite. The `user_id` columns and composite keys are already in place.

## 11. Testing strategy

Every test except the live suite runs without a model: recorded LLM responses are stored in the cache format, and a fake LLM client serves them and fails on any cache miss.

| Level | Scope | LLM | When |
| --- | --- | --- | --- |
| Unit | Rules, ScoringEngine, Optimizer (synthetic judgements), Validator, element parser, sentence splitter, mapping tables | none | every commit |
| Task | Each LLM task: prompt renders, schema validates, recorded responses parse | fake, from recordings | every commit |
| Pipeline | F1, F3, F4, F5, F6, F7 end to end on the golden data set | fake, from recordings | every commit |
| Live | The golden scenarios against the real provider; asserts the acceptance criteria, not exact wording | real | on demand |

**Golden data set**

- A test user with three positions (one current, one ended more than five years ago), an English CV, a German reference letter with one very good and one coded-satisfactory passage, a diploma, and one learnable claim.
- Four JDs: English, German, one requiring a degree the user lacks, and one saved web page containing an embedded instruction aimed at the model.

**Specific checks**

- Every acceptance criterion of P1 §10 maps to at least one automated test, mostly by reusing the Validator and ScoringEngine.
- Isolation: with two users in one database, no operation under one user id reads or changes the other user's rows or files.
- Property tests: impacts plus gap points equal 100 for random judgements; position ordering; provability derivation over random evidence sets.
- Interactive review: driven by scripted terminal input, including skip, edit, quit and resume.
- Renderer tests: page count, text-extraction order, and marks on exactly the exaggerated and invented statements.
- Recording mode: running the live suite with recording switched on refreshes the recordings after a prompt-version change.

## 12. Refinements to phase 1 and open questions

The architecture sharpens six points of phase 1, all confirmed and folded into the phase 1 document, and settles three further choices. Phase 3 changes C1–C4 (P3 §13.1) are folded into sections 5–7.

### 12.1 Refinements to phase 1

| # | Refinement | P1 section |
| --- | --- | --- |
| R1 | Claim status is `confirmed` or `proposed` only; rejection is recorded on proposal items. | §4, F1 |
| R2 | An invented skill is placed once: in the skills section or as a highlight, whichever scores higher. | §6.6 |
| R3 | An `exact` or `synonym` keyword hit counts only if the meaning check does not answer `none`. | §8.2 |
| R4 | Years-of-experience points split across the counted positions by months. | §8.5 |
| R5 | Run folders also hold `run.json` (with the as-of date) and `candidates.json`. | §9.1 |
| R6 | Scoring an edited CV yields no dropped rows; elements whose text changed get blank fidelity and provability. | §9.3 |

### 12.2 Settled choices

1. **LLM provider and data.** A cloud LLM API behind the `LLMClient` port, with a strong and a fast model. Sending CVs and reference letters to that provider is accepted.
2. **Storage.** One shared database for all users, partitioned by user id (D4, section 4).
3. **Review UX.** Interactive prompts in the terminal, item by item (8.3).

The typesetting system, OCR approach and libraries are phase 3 choices.
