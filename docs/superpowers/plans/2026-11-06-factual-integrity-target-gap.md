# Factual integrity, target-gap reporting, and CLI improvements

**Goal:** Implement the user's approved 17-step plan, retaining the aspirational CV and excluding guided gap resolution and a web UI.
**Architecture:** Keep domain calculations pure; extend ports, existing services and CLI. Add focused modules where the generation service would otherwise grow substantially.
**Spec:** The user's implementation request in this conversation; the requirements below reproduce its binding behavior.

## Global Constraints
- Build on the existing service architecture and CLI.
- Keep the aspirational CV central; clean output does not imply that every statement is supported.
- Abort on persistently unverifiable translation rather than silently omitting confirmed experience.
- Suggested actions are static report content. No interactive gap interviews, guided resolution workflow, web UI, or estimated learning-effort claims.
- Preserve user isolation, JSON Resume compatibility, non-LLM commands without an API key, and backward-readable legacy files.
- Tests use synthetic users/data and no real provider calls. Do not read .env or personal output/ data.
- Use regression-first testing; record failing and passing commands. Do not fabricate recorded provider responses: new fixtures must be clearly identified as synthetic.
- Run focused tests during iteration and the offline suite before task completion. Run lint, format check, mypy and import contracts at integration.
- Do not push or merge. Work on feat/evidence-target-gap in the current checkout. No subagents from implementation/review workers.

## Review Focus
- Unknown legacy provenance or usage must be unavailable, not silently trustworthy or zero.
- Stale review proposals must not attach old evidence to a newly edited assertion.
- Read operations must pin a generation rather than combine different published artifacts.
- Supported profile coverage must include full untruncated held claims omitted by target selection.
- Inference, partial support and missing profile information must not become claims of actual user incapability.

### Task 1: Original-fact protection and scoring integrity

Implements approved steps 1, 2, and scoring/cache portions of 7.
- Keep original Claim objects immutable alongside translated display versions. Faithful translations must preserve names, numeric quantities (digits and spelled EN/DE quantities), units and associations; retry with the existing task runner and then abort before publication on persistent failure. Sourced variants that fail are discarded. Keywords are hints, never source assertions. Protect unmodified translations and fallback paths too.
- Extend deterministic factual checks with entity extraction/context-aware verification using the existing classification/task infrastructure where needed; detect swapped quantities as well as new quantities/names. Avoid treating ordinary names containing numeric words as quantities. Retain intentional invention/exaggeration; explicit numeric quantities are forbidden in invented content.
- Attribute fallback position titles to explicit fixed-record sources rather than arbitrary claims. Add backward-compatible source_record_refs to Candidate/Annotation and validation provenance where needed.
- Pair cache keys include the complete judged requirement (text/category/keywords/synonyms) and use a new cache version.
- Preserve confirmed degree classifications in namespaced JSON Resume metadata keyed by education pointer and entry fingerprint. Trust only when the corresponding entry still matches; external/edited entries fall back to inference.
- Tests: original 2 FTE -> translated 200 rejected; equivalent localized quantities accepted; changed client names/spelled quantities/swapped relationships rejected; unmodified/fallback protection; invention numeric checks; changed category/keywords/synonyms invalidates pair cache; explicit degree override survives generation and loses authority after editing.
- Interfaces: retain original_text compatibility for current report code; new original_claims state may be added. Any new metadata helpers belong in domain modules. Record new contracts in the task report for subsequent workers.

### Task 2: Evidence validity, safe review, and record matching

Implements approved steps 3-6 and matching portion of 7.
- Migrate evidence and fact_source links with assertion snapshots and active/stale state; expose typed fact-source provenance through repository ports. Substantive edits to claim text/position or fixed-record assertions stale source links, retaining history. Provability uses active evidence only. Explicit claim edits attach self_declared or, for learnable-only claims, learnable_declared evidence.
- Validate owned document/type for document evidence, mandatory supporting reference-letter excerpt, supplied excerpt membership in extracted text, and reference-person name. Add excerpt capture/checks to extraction outputs; use optional fields for historical fixtures, and mark truly missing historical excerpts as unavailable. Invalid legacy source attachments are retained but excluded from provability; migration recomputes affected claims. Valid legacy assertions are snapshotted as current without claiming historical re-verification.
- Add claim confirm ID and evidence reconfirm ID. Proposed claims require explicit confirmation with active evidence; simply adding evidence does not auto-confirm. An accepted reviewed AddEvidence can confirm.
- Store expected assertion fingerprints on evidence/fact-source proposals. Preflight all accepted actions before applying any records. Persist stale proposals as pending conflicts and require renewed review; no partial apply. Also prevent old SetProfileField from overwriting a newer nonempty value. Refresh pending items after cascade rejection; accepting unresolved/rejected dependencies is refused unless edited to a valid reference.
- Awards/certificates deduplicate on normalized name+issuer+date, preserve distinct known issuers/dates, and expose missing-identity ambiguity as a review conflict rather than silently dropping data. Add award to conflict record types. Missing-field enrichment must preserve confirmed data.
- Tests: edited high claim becomes appropriately declared; stale links/history; attachment validation and isolation; migration/backfill/downgrade; restoration/confirm/reconfirm; stale AddEvidence/LinkFactSource/profile proposals; reject parent then accept child; batch rollback; repeated awards/certifications.
- Interfaces: expose snapshot/provenance records and active source queries for Task 4; existing repository readers remain compatible where practical. Record exact APIs in report.

### Task 3: Isolated runs, input/provider failures, and atomic publication

Implements approved steps 8, 9, and 14.
- Resolve owned run names/prefixes/absolute paths only to direct run children after realpath; reject traversal and escaping symlinks. Add scoped FileStore helpers as needed.
- Map Anthropic SDK/network/status errors to LLMTaskError with safe actionable messages; keep missing-key InputError behavior.
- Detect image-only pages individually. If mixed PDF needs OCR, transcribe the full document in order without duplicating extracted text, preserving hyperlink targets. Truly blank pages alone do not force OCR.
- Stage a complete generation in .generations/<id>, validate both clean cv.pdf and cv-annotated.pdf, then atomically replace one current-generation symlink. Preserve root artifact paths as aliases through that pointer. Adopt legacy outputs as an initial generation before conversion. Keep editable requirements.json input at root; put requirements snapshot, generation run.json, profile snapshot, usage and all output artifacts behind the pointer. Initial run metadata remains readable before first generation. Pin the resolved generation for service/render/scoring multi-file reads.
- Failed attempts preserve current artifacts and record separate safe attempt status so list/show can distinguish failure with an earlier successful publication. No sensitive exception text in attempt metadata.
- Tests: cross-user paths/absolute/symlink escape, SDK errors, mixed scan vs blank page, render/publication fault injection including legacy adoption, pinned reads, clean+annotated outputs, existing clean render compatibility.
- Interfaces: document FileStore/run artifact helpers and generation attempt metadata for Task 5. Publication should expose a staging mechanism Task 4 can use to add profile/report snapshots, and Task 5 to add usage.

### Task 4: Full-profile coverage, provenance, and explanatory reports

Implements approved steps 10-13.
- Add an independently computed profile-supported score from all confirmed held medium/high claims and fixed facts. Use complete originals with verified translations, no line/page limit, neutral placement 1. Skills are full skill elements; experience/achievement are highlights with optional position link. Only linked dated highlights count toward years. Low learnable-only claims are excluded from this score. Keep target CV scoring unchanged and allow negative target-minus-profile differences.
- Snapshot complete confirmed profile facts/claims plus active/stale evidence provenance for the run. Structured provenance includes original wording, source claim/record refs, document IDs/names, available excerpts, inferred sources, and transformation reasons, including zero-impact target statements.
- Add per-requirement text/category/importance, profile and target coverage, support refs, target statements, gap labels and static next actions. Labels can coexist: wording (supported rephrasing improves match), evidence (self-declared/stale), transferable (explicit inferred sources), partial support (positive incomplete support), unsupported in profile (none), qualification (structured requirement shortfall), omitted for space (actual budget-drop reason). Logistics remain separate. Describe uncertainty, not actual lack of capability.
- Report JSON/CSV/PDF lead with both explained metrics and requirements, distinguish selected/gap/dropped content, show sources/drop reasons, preserve annotation rows and EN/DE labels. Keep legacy Report parsing with defaults: unknown supported score is None, not zero. Standalone external CV reports without matching provenance leave profile coverage unavailable.
- Tests: held-but-dropped long/untied claims count; low excluded; year union; different target/support winners; classifications with actual budget causes; provenance including stale/missing excerpts/zero impact; standalone and legacy parsing; CSV and EN/DE PDF text/visual QA. Update relevant README positioning.
- Interfaces: expose a reusable profile coverage service/result and snapshot fingerprint for Task 6, sharing date/config consistently; document APIs in report.

### Task 5: Progress, usage/cost visibility, and run discovery

Implements approved steps 15-16.
- Add run list and run show (both --json): statuses, publication and last-attempt dates, scores where available and artifact paths, preserving valid publication after failed regeneration. All paths and records are user-scoped.
- Introduce optional observer interfaces outside the domain for stage progress/durations, LLM task/cache counters, and token usage. Extend LLMResult with optional usage, separating input/output/provider cache read/write tokens. Aggregate all observable calls including invalid/refused/truncated responses and retries. Distinguish application cache hits (no new billing) from provider cache billing. Mark unobservable usage incomplete.
- Add optional per-model USD-per-million-token price settings; do not guess model prices. Known subtotal and total completeness are distinct; unknown usage/rates show incomplete/unavailable total, not false zero. Capture operation summary in published usage.json and safe attempt status even when generation fails. Keep prior historical usage unavailable. Track JD intake and generation without losing either contribution.
- CLI progress works on terminals without corrupting machine JSON/non-TTY output. Preserve commands without API keys.
- Tests: list/show legacy/current/failed runs/isolation; stage observer; known/unknown/incomplete usage and prices; retries; application/provider caches; JSON cleanliness; both PDFs discoverable. Update README command/config descriptions.
- Interfaces: document observer/usage types and discovery APIs; no external telemetry transmission.

### Task 6: Role comparison and whole-application integration

Implements approved step 17 and final integration.
- Add run compare RUN [RUN...] with at least two distinct owned runs, --as-of, --json, optional --out DIR. Evaluate selected edited JD requirements against one current profile snapshot, common date (today default), and effective config; use Task 4 profile coverage service.
- Rank supported coverage descending, must-requirement weighted gap ascending, stable run_id ascending. Saved target scores are displayed with generation date and stale fingerprint/requirements/config/date information; do not silently regenerate targets. Recurring gaps group only identical normalized complete requirement signatures (text/category/structural fields, not importance/IDs); prioritize summed weighted missing points with units clearly labeled across roles. No learning-effort estimates.
- Export comparison.json/csv only when --out requested. No guided gap actions/UI. Update README and developer docs for full delivered behavior.
- Tests: common snapshot/date/config, overlapping years, distinct/same-prefix IDs, same normalized requirement vs overlapping keywords, stale/legacy targets, file exports, isolation, complete CLI workflow. Run all offline pytest, ruff check/format check, mypy, lint-imports; visually inspect EN/DE PDFs with Poppler.
- Preserve and address integration regressions from prior tasks; no unrelated feature expansion. Final independent whole-branch review follows.
