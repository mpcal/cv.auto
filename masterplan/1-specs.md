# CV Optimizer — Phase 1: Functional Specification

Oct 16, 2026 · @Michael

## 1. Purpose and scope

The system shows a user what an ATS-optimal CV for a given job description (JD) would look like, built from their own claim database, and reports exactly where that CV departs from what they can back up. It is a gap-analysis tool: the output is a target to grow toward and a draft to rewrite, not a CV to submit as generated. Users must remain comfortable discussing whatever they finally send.

**In scope (v1)**

- A plain Python application: a command-line interface over a service layer. Every operation acts on behalf of one user, identified by email (section 2).
- A per-user claim database, filled manually and from documents (CVs, reference letters, diplomas, certificates), with a user review step.
- JD intake from a link, a PDF or pasted text.
- Generation of an optimized CV in JSONResume format, plus an annotation file and a report.
- A documented, reproducible ATS scoring model with per-statement impact attribution.
- Typeset PDF output: annotated for generated CVs; clean for any JSONResume file on explicit request.
- Export and deletion of all data belonging to a user.

**Out of scope (v1)**

- Letter of motivation.
- Authentication, web API, MCP server and web UI. These come later; the design keeps them possible (section 2).
- In-app CV editing. Users edit the exported JSON elsewhere.
- Feeding edited CVs back into the database, and CV version history.

## 2. Users and identity

The system is multi-user by design, but v1 runs as one user whose id (an email address) is supplied from outside — by configuration or a command-line argument. Later, the same id will come from an authentication layer; nothing else changes.

- **User id:** an email address, normalized to lower case. It is the only identity concept in v1.
- **Partitioning:** every stored record carries the user id. Every operation takes the user id as its first input and can only read or write that user's records.
- **No cross-user features:** no sharing, no shared claims, no global claim catalogue.
- **Unknown user:** the first operation for an unknown id creates an empty profile for it.

## 3. Glossary

Two layers matter throughout: the database holds **claims** and **fact records** about the user; a generated CV consists of **statements** derived from them.

| Term | Definition |
| --- | --- |
| Claim | A database entry asserting something the user can do or has done. Types: `skill`, `experience`, `achievement`. Optionally tied to one position. |
| Fact record | A hard fact about the user: profile (name, contact), position, education, language, certificate. Never optimized, never invented. |
| Position | A job held: employer, title, location, start date, end date (or current). Replaces the original "claim of a company worked for". |
| Source document | A file the user provided: CV, reference letter, diploma, certificate, other. |
| Evidence | A link from a claim to what backs it: a source-document excerpt, a named reference person, or the user's own declaration. |
| Provability | How well the user could back a claim or statement: `high`, `medium`, `low`, `none` (section 7). |
| JD | The job description a CV is generated for. |
| Requirement | One thing a JD asks for, with category, importance and weight (section 8). |
| Statement | One unit of content in a generated CV: the headline, a summary sentence, a work highlight, a skill keyword. The unit of the report. |
| Fidelity | How a statement relates to its source claims: `unmodified`, `rephrased`, `exaggerated`, `invented` (section 7). |
| ATS score | A 0–100 score of a CV against a JD, defined by this system's own scoring model (section 8). |
| Impact | The share of the ATS score attributed to one statement or fact record, in percentage points (pp). |
| Run | One generation of a CV, annotations and report for one JD. |

## 4. User database

Each user owns one profile, any number of fact records, claims and source documents. Claims optionally point to one position; each claim has one or more evidence entries; evidence may point to a source document. Every entity below also carries `user_id`, `created_at` and `updated_at`.

| Entity | Fields | Notes |
| --- | --- | --- |
| Profile | name, contact email, phone, location (city, country), website, online profiles (network, url) | One per user. Maps to JSONResume `basics`. Contact email may differ from the user id. |
| Position | employer, employer location, title, start date, end date or "current", source documents | Dates at month precision (YYYY-MM). Never altered by optimization. |
| Education | institution, area, degree type, start date, end date, grade (optional), source documents | Maps to JSONResume `education`. Never optimized. |
| Language | language, level (CEFR A1–C2 or "native"), source documents | Maps to JSONResume `languages`. Never optimized. |
| Certificate | name, issuer, date, url (optional), source documents | Maps to JSONResume `certificates`. Never optimized. |
| Claim | type (`skill`, `experience`, `achievement`), text, text language, position (optional), keywords, status (`proposed`, `confirmed`), origin (`extracted`, `manual`) | Text is a single assertion in the user's words or the document's. Keywords are normalized terms (e.g. "TensorFlow", "team leadership") used for matching. Only `confirmed` claims are used for generation. |
| Evidence | claim, kind, source document (optional), excerpt (optional), reference person name and contact (optional) | Kinds: `reference_letter`, `certificate_or_diploma`, `reference_person`, `own_cv`, `self_declared`, `learnable_declared`. |
| Source document | kind (`cv`, `reference_letter`, `diploma`, `certificate`, `other`), original file, extracted text, language, issuer, related position (optional), received date | The original file is kept so evidence excerpts stay checkable. |

**Stored provability** of a claim is derived from its evidence, taking the highest level present:

- `high`: any `reference_letter`, `certificate_or_diploma` or `reference_person` evidence.
- `medium`: any `own_cv` or `self_declared` evidence.
- `low`: only `learnable_declared` evidence ("I could pick this up quickly").

`none` is never stored; it exists only for generated statements (section 7). Fact records get `high` when a third-party source document backs them, else `medium`.

Generation runs are not stored in the database in v1. Their outputs are written as files (section 9).

## 5. Functions

Eight functions cover the system: two fill the database (F1, F2), five turn a JD into outputs (F3–F7), one handles the user's data as a whole (F8). Every function takes the user id as input.

### F1. Ingest a document

Input: a file (PDF, DOCX, or a scanned PDF/image) and its kind as declared by the user.

1. Store the original file, extract its text (OCR when needed), detect its language. A file already ingested for this user (same content hash) is reported and skipped.
2. Extract proposals according to kind:
   - CV: profile fields, positions, education, languages, certificates, and claims with `own_cv` evidence.
   - Reference letter: issuer (employer, author), period covered, the related position, and claims with `reference_letter` evidence plus the supporting excerpt.
   - Diploma or certificate: the education or certificate record, and skill claims it attests with `certificate_or_diploma` evidence.
3. Interpret reference letters by meaning, not wording. German-language letters (Arbeitszeugnisse) and similar use coded grading where faint praise is negative. A passage counts as evidence only if its coded meaning is good or very good; weaker passages yield no evidence and are shown to the user as a note.
4. Match proposals against the existing database:
   - Position: same employer (tolerant name match) with overlapping dates is the same position. Only missing fields are proposed.
   - Claim: an existing claim with the same assertion, same position (or both untied) and same quantities is the same claim. The proposal becomes "add evidence to existing claim".
   - Conflict: differing dates, titles or quantities for the same item are flagged and never resolved automatically.
5. Review: the user sees every proposal (new record, new claim, added evidence, conflict) and accepts, edits or rejects each one; bulk accept is allowed. Until accepted, extracted claims stay `proposed` and are not used for generation; rejected proposals stay on the review batch and never become claims.

### F2. Manage data manually

- Create, read, update and delete the profile, positions, education, languages, certificates and claims.
- New manual claims take evidence `self_declared` (medium) or `learnable_declared` (low). The user may attach a source document or a named reference person to raise a claim to high.
- Deleting a source document deletes the evidence pointing to it and recomputes provability. A claim left without evidence returns to `proposed` for review.

### F3. Take in a JD

Input: a URL, a PDF, or pasted text.

- URL: fetch the page and extract the main content. A failed fetch, a login wall, or text that does not read as a job description ends with a clear message asking for a PDF or pasted text.
- Output: the JD text, job title, company, language, and the requirement list (section 8), saved as files of the run.
- The user may edit the saved requirement list (e.g. change an importance) and generate from the edited list.

### F4. Generate an optimized CV

Input: the JD's requirement list and the user's confirmed database content. Output: a JSONResume CV and its annotation file, produced under the rules of sections 6 and 7 and optimized against the score of section 8.

### F5. Score a CV

Input: any JSONResume file and a requirement list; an annotation file is optional. Output: the total score, coverage per requirement and impact per statement. F5 also works standalone, so users can score their own edited CV against the same JD. Its report then has no dropped rows, and statements whose text no longer matches their annotation get blank fidelity and provability.

### F6. Build the report

Input: a scored CV and its annotations. Output: the report described in section 9.

### F7. Render

- Annotated render (default for a run): the generated CV with exaggerated and invented statements visibly marked, plus the report as an appendix.
- Clean render (explicit request): any JSONResume file, typically the user's edited version, without marks.

### F8. Export and delete user data

- Export: all database records and source documents of the user as one archive.
- Delete: all records, documents and run outputs of the user, irreversibly, after an explicit confirmation.

## 6. Optimization rules

The optimizer maximizes the ATS score (section 8) within a length budget, changing only statements and never hard facts. When alternatives score the same, the less altered and better provable CV wins.

### 6.1 CV structure

Sections in order of appearance:

1. Basics: profile fields as stored; headline (`label`) and summary are optimized.
2. Professional experience (`work`): every position, optimized highlights.
3. Education: all entries, as stored.
4. Skills: optimized.
5. Languages: all entries, as stored.
6. Certificates: all entries, as stored.

### 6.2 Positions

- Every position appears, so the timeline shows no gaps that are not real.
- Order: current positions first, then by end date descending; ties by start date descending.
- Employer, title, location and dates appear exactly as stored.
- Highlights are selected, ordered by impact (highest first), and rephrased, exaggerated or invented as 6.4 allows.
- A position may end up with zero highlights. If space remains, it receives its most relevant sourced claim, most recent position first.
- Position descriptions (`work.summary`) are not used in v1.

### 6.3 Statements

Only these are optimized; each is one statement with one fidelity:

- Headline: one statement.
- Summary: 2–4 sentences, one statement each.
- Work highlights: one statement each.
- Skill keywords: one statement each. Skill group names are headings, not statements. Skill levels are not emitted.

A statement never mixes sourced and invented content. Invented content goes into its own statement.

### 6.4 What a statement may do

- **Unmodified:** the source claim's text, or a faithful translation of it.
- **Rephrased:** same meaning and scope in different words, e.g. the JD's terminology, or several claims of the same position merged into one statement.
- **Exaggerated:** stronger verbs, broader scope or more ownership than the source supports. Allowed only if it gains at least 1.0 pp over the best rephrased alternative.
- **Invented:** asserts something no confirmed claim supports. Added for every requirement not fully covered by sourced content, unless the requirement concerns a hard fact (6.5).

### 6.5 Hard facts are never invented or altered

- Profile and contact data; employers, titles, locations and dates of positions.
- Education, languages and certificates. Requirements for these stay uncovered and appear in the report.
- Numbers, dates, quantities and proper names (tools, products, clients) inside a source claim. Exaggeration changes wording, not figures: "saving 2 FTEs" never becomes "saving 5 FTEs".
- Invented statements contain no figures at all.

### 6.6 Placement of invented statements

- Skills go into the skills section or into a highlight, whichever scores higher — never both.
- Experience and achievements go under the most plausible position: the most recent one whose role and domain make the statement credible, else the most recent position.
- Each invented assertion appears once in the whole CV.

### 6.7 Length budget and selection

- The rendered CV fits 2 A4 pages (configurable).
- Over budget, statements are dropped in order of lowest impact first. Positions and fact records are never dropped.
- Statements with zero impact are dropped, except as filler under 6.2.

### 6.8 Language and tone

- The CV is written in the JD's language; source claims are translated as needed.
- Every statement is phrased with full confidence, without hedging, whatever its provability.
- Where meaning allows, statements use the JD's exact terms, since exact matches score highest (section 8).

### 6.9 Objective and tie-breaks

Maximize the total score. Among alternatives within 0.1 pp of each other, prefer in order: fewer invented statements, fewer exaggerated statements, higher provability, shorter text.

## 7. Provability and fidelity

A statement's provability follows from its fidelity and the provability of its source claims: rewording keeps the level, exaggeration costs one level, invention starts at `low` or `none`.

**Provability levels**

- `high`: backed by a third-party document or person: reference letter, diploma, certificate, named reference.
- `medium`: self-asserted, including the user's own CV.
- `low`: not yet held but credibly learnable: declared by the user as learnable, or inferred by the generator from related confirmed claims (C++ → Rust).
- `none`: no basis in the user's data.

**Statement provability by fidelity and source provability**

| Statement fidelity | Source `high` | Source `medium` | Source `low` |
| --- | --- | --- | --- |
| unmodified | high | medium | low |
| rephrased | high | medium | low |
| exaggerated | medium | low | low |
| invented | low if inferable, else none | low if inferable, else none | low if inferable, else none |

**Rules**

- A statement built from several claims takes the lowest provability among them.
- An invented statement is `low` only if it is a close substitute of a confirmed skill (same category, transferable) or a natural extension of confirmed experience at a similar level. The generator records which claims it infers from; the report shows them.
- A learnable claim's text names the competence itself ("Kubernetes"), not the hedge. The hedge lives in its evidence kind, so stating the competence counts as `unmodified` with provability `low`.
- Fact records shown in the report are `unmodified`; their provability follows section 4.

## 8. ATS scoring model

The score is weighted requirement coverage: each JD requirement contributes its weight times how well the best CV element covers it, and that element earns the credit. It approximates how keyword and semantic matchers rank CVs; it is not any vendor's score. All numbers below are tunable defaults (section 11).

### 8.1 Requirements

The JD is split into requirements, each with: id, text as in the JD, category, importance, keywords (the JD's exact terms plus synonyms, acronyms and spelling variants), and an optional quantity (years, level). "A and B" becomes two requirements; "A or B" becomes one with alternative keywords.

| Category | Example | Covered by | Coverable by invention |
| --- | --- | --- | --- |
| hard\_skill | TensorFlow | skill keywords, highlights, headline, summary | yes |
| soft\_skill | stakeholder management | highlights, summary, skill keywords | yes |
| experience | led data science teams | highlights, headline, summary | yes |
| domain | banking industry | highlights, summary | yes |
| years\_experience | 5+ years of Python | positions holding a matching highlight | only via 6.6 placement |
| education | MSc in computer science | education entries | no |
| certification | AWS Solutions Architect | certificates | no |
| language | German C1 | languages | no |
| logistics | work permit, location, travel | — | not scored; listed in report |

Importance is `must` (required, must, essential) or `nice` (preferred, a plus, ideally). Weights: must = 3, nice = 1.

### 8.2 Match of a requirement against one CV element

| Level | Value | Condition |
| --- | --- | --- |
| exact | 1.0 | A JD keyword appears (case-insensitive, whole word) and the meaning fits. |
| synonym | 0.9 | A listed synonym, acronym or variant appears and the meaning fits ("ML" for "machine learning"). |
| semantic | 0.6 | Same meaning without any listed keyword. |
| partial | 0.3 | Related but weaker: adjacent tool, lower seniority, narrower scope. |
| none | 0.0 | No relation. |

Category-specific rules replace the table where they apply:

- Language: level at or above the required one = 1.0; one CEFR level below = 0.5; else 0.
- Education: degree level met and field matching = 1.0; level met, related field = 0.6; one level below = 0.3; else 0.
- Certification: the named certificate or a listed equivalent = 1.0; else 0.
- Years of experience: covered years = the union of the date ranges of positions holding a highlight that matches the skill at `semantic` or better; a current position counts up to the run date. Match = min(1, covered years ÷ required years).

### 8.3 Placement factor

| Where the element sits | Factor |
| --- | --- |
| Highlight of a current position or one ended less than 5 years ago | 1.0 |
| Highlight of an older position | 0.85 |
| Headline or summary | 0.8 |
| Skills section only | 0.7 |
| Education, language or certificate entry | 1.0 |

### 8.4 Score

Coverage of requirement r is its best match over all CV elements e, discounted by placement:

```latex
c_r = \max_e \; m(r, e) \cdot p(e)
```

The total score over all scored requirements R:

```latex
S = 100 \cdot \frac{\sum_{r \in R} w_r \, c_r}{\sum_{r \in R} w_r}
```

### 8.5 Impact attribution

- Each requirement's points go to the element that achieves its coverage (the winner); tied winners split them equally. Years-of-experience points split across the counted positions by their months in the union (each month counted once, for the covering position with the latest start), and within a position go to its best-matching highlight.
- Impact of an element = the sum, over requirements it wins, of 100 × w\_r × c\_r ÷ Σw. The impacts of all elements sum to S.
- An element that wins nothing has impact 0 pp.
- For each requirement, the missing points 100 × w\_r × (1 − c\_r) ÷ Σw are reported as its gap.

### 8.6 Reproducibility

Scoring the same CV against the same requirement list always gives the same result. Match levels that need language understanding (`semantic`, `partial`) are judged once per requirement and element text and then reused.

## 9. Outputs

Each run writes one folder of files per user: the JD and its requirements, a schema-valid JSONResume CV, a separate annotation file, the report, and the annotated PDF. The CV file carries no annotations, so any JSONResume tool can open it.

### 9.1 Run folder

| File | Content |
| --- | --- |
| `jd.txt` + original | The normalized JD text, plus the original PDF or the URL. |
| `requirements.json` | The requirement list (8.1). Editable; generation can be rerun from it. |
| `cv.json` | The optimized CV, valid against the JSON Resume schema, no extra fields. |
| `annotations.json` | One entry per statement (9.2). |
| `report.json`, `report.csv` | The report (9.3), for programs and for spreadsheets. |
| `cv-annotated.pdf` | The annotated render (9.4) with the report as appendix. |
| `run.json` | Run id, creation time, as-of date (reused when the CV is rescored), JD source, language, model ids, prompt versions, configuration snapshot. |
| `candidates.json` | Every candidate statement with its judgements and why it was or was not selected. |

Folder name: run date and time, company and job title, e.g. `2026-10-16T2334-acme-senior-data-scientist`.

### 9.2 Annotation file

One entry per statement:

- **Location:** a JSON pointer into `cv.json` (`/basics/label`, `/work/0/highlights/2`, `/skills/1/keywords/3`); for summary sentences, `/basics/summary` plus a sentence index.
- **Text:** a copy of the statement, so later edits to `cv.json` can be detected.
- **Fidelity and provability.**
- **Source claims** (ids and original texts); for `low` inventions, the claims inferred from.
- **Requirements addressed** (ids) and **impact** in pp.

### 9.3 Report

The report is a table with one row per CV statement, per fact record that wins a requirement, per uncovered or partly covered requirement, and per relevant dropped claim.

| Column | Content |
| --- | --- |
| Claim | Statement text; for gap rows the requirement text; for dropped rows the claim text. |
| Fidelity | `unmodified`, `rephrased`, `exaggerated`, `invented`; empty for gap rows. |
| Provability | `high`, `medium`, `low`, `none`; empty for gap rows. |
| Impact (pp) | Points won (8.5); for gap rows the missing points; 0 for dropped rows. |
| Requirement | The JD requirement(s) the row addresses. |
| Source | Source claim text(s), or for dropped rows the reason (budget, superseded). |
| Row kind | `in CV`, `gap`, `dropped`. |

Rows are sorted by row kind, then impact descending. A dropped claim is relevant if it matches some requirement at `partial` or better.

Above the table:

- Total score S, and the score split by fidelity and by provability. Score plus gap points sum to 100.
- Logistics requirements (not scored), listed for the user.
- Timeline gaps longer than 3 months between positions, as information only.

### 9.4 Renders

- **Annotated:** the generated CV with exaggerated and invented statements each visibly marked in a distinct way, each statement numbered against its report row, and a legend. A header line names the JD and date and labels the document as an annotated target CV.
- **Clean:** any JSONResume file, without marks.
- Both renders are ATS-readable: single column, standard section headings in the CV's language, selectable text, no content inside tables, images or text boxes.

## 10. Acceptance criteria

The build is accepted when every check below passes on a test user with at least three positions, one reference letter and one JD per supported language.

**Database and ingestion**

- [ ] Every stored record carries the user id; operations under one user id never read or change another user's records.
- [ ] Ingesting the same file twice creates no new records.
- [ ] No extracted claim is used for generation before the user confirms it.
- [ ] A claim's provability equals the highest level among its evidence and is recomputed after every evidence change.
- [ ] A claim found in both a CV and a reference letter exists once, with two evidence entries and provability `high`.

**JD intake**

- [ ] An unreadable URL ends with a message asking for a PDF or pasted text, and no run folder is created.

**Generation**

- [ ] `cv.json` validates against the JSON Resume schema.
- [ ] Every stored position appears exactly once, ordered as in 6.2, with employer, title, location and dates identical to the database.
- [ ] Education, languages and certificates in `cv.json` are identical to the database.
- [ ] Every statement in `cv.json` has exactly one annotation entry, and every annotation points to an existing statement.
- [ ] No exaggerated statement contains a number, date or proper name missing from its source claims; no invented statement contains a figure.
- [ ] Every exaggerated statement has at least one source claim and beats its best rephrased alternative by at least 1.0 pp.
- [ ] No invented assertion appears twice.
- [ ] Every statement's provability matches the table in section 7.
- [ ] The rendered CV fits the page budget, and its language is the JD's language.

**Scoring and report**

- [ ] Impacts of all `in CV` rows sum to S within 0.1 pp; S plus all gap points equals 100 within 0.1 pp.
- [ ] Every requirement with coverage below 1 appears as a gap row.
- [ ] Scoring the same files twice gives identical results.

**Renders and data handling**

- [ ] The annotated PDF marks exactly the exaggerated and invented statements; a clean render shows no marks.
- [ ] Text extracted from either PDF contains every statement, in reading order.
- [ ] After deletion, nothing of that user remains, and other users' data is unchanged.

## 11. Decisions

All twenty decisions are settled, and the six refinements from the architecture phase (P2 §12.1) are folded in.

### 11.1 Decisions

| Decision | Basis |
| --- | --- |
| Letter of motivation is out of scope. | User |
| Users can store learnable (`low`) claims manually. | User |
| All positions appear in reverse chronological order so no false timeline gaps arise; highlights within a position may be dropped. | User (highlights: interpretation) |
| v1 is a plain Python app without auth, web API or MCP server, acting for one user id (email) supplied from outside. | User |
| Hard facts are never invented or altered (6.5). | Accepted proposal |
| Annotations live in a separate file; generated CVs render annotated. | Accepted proposal |
| `high`/`medium` come from evidence; `low`/`none` also arise at generation; matrix in section 7. | Accepted proposal |
| The system defines its own scoring model; impact is attributed per requirement and sums to the score. | Accepted proposal |
| The report adds gap and dropped rows plus requirement and source columns. | Accepted proposal |
| Sections include headline, summary, languages and certificates. | Accepted proposal |
| Positions are first-class records; claims are deduplicated; extracted claims need review; coded reference letters are decoded. | Accepted proposal |
| JD intake has a pasted-text fallback. | Accepted proposal |
| In-app editing, edit feedback into the database, and CV history are out of scope. | Accepted proposal |

### 11.2 Confirmed defaults

1. **Interface.** v1 is a command-line interface over a Python service layer. A web API and MCP server will later wrap the same layer; the React UI arrives with them.
2. **Clean render.** Kept as an explicit, separate function for any JSONResume file (F7).
3. **Languages.** The CV is written in the JD's language; English and German are required, others best effort.
4. **`low` by inference.** The generator may mark an invented statement `low` when it judges it inferable from confirmed claims (section 7).
5. **Years of experience.** Invented highlights count toward years of experience for the position they sit under (8.2), limited by the placement rule (6.6).
6. **Reference persons.** A named reference person counts as `high` evidence.
7. **Timeline gap notice.** The report lists gaps over 3 months as information (9.3).

### 11.3 Tunable defaults

| Parameter | Default | Section |
| --- | --- | --- |
| Weight of `must` / `nice` requirements | 3 / 1 | 8.1 |
| Match values: exact, synonym, semantic, partial | 1.0, 0.9, 0.6, 0.3 | 8.2 |
| Placement: recent highlight, older highlight, headline/summary, skills only | 1.0, 0.85, 0.8, 0.7 | 8.3 |
| Recency cut-off for highlights | 5 years | 8.3 |
| Minimum gain for an exaggeration | 1.0 pp | 6.4 |
| Tie window for the objective | 0.1 pp | 6.9 |
| Length budget | 2 A4 pages | 6.7 |
| Summary length | 2–4 sentences | 6.3 |
| Timeline gap threshold | 3 months | 9.3 |
