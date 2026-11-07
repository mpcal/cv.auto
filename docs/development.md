# Current integrity and comparison contracts

The layered CLI/service/domain/port architecture remains in place. JSON Resume CVs are
aspirational targets: profile-supported coverage is a separate current or snapshotted
measurement. No guided gap-resolution workflow or learning-effort model is implemented.

`Container.comparison.compare(user, refs: list[str], *, as_of: date | None = None)` returns
`domain.comparison.Comparison`. It requires at least two distinct resolved owned run paths.
Name/prefix/absolute references use FileStore ownership resolution; two aliases for one run
are rejected. Distinct folders remain distinct roles even if legacy metadata reused a run
ID; folder name breaks such ties and identifies recurring-gap provenance. Missing run IDs
fall back to owned folder names. Full IDs and names are preserved in JSON/CSV.

The service discovers/pins saved artifacts once, reads current editable requirements, calls
`ProfileCoverageService.load(user)` once, then calls `coverage(..., facts=common_facts)` per
role with one date/current settings. Translation starts from original stored wording each
time; failed fidelity verification aborts. ProfileCoverageService and the pure scorer own
support eligibility, typed qualifications and union-of-months experience handling.

`Comparison.roles` ranks current support descending, must gap points ascending, then run ID
and name. It includes each complete requirement, support coverage/weighted missing points,
and typed support refs/date dependencies. The shared profile snapshot preserves original
assertions and source state. Saved target scores come solely from discovery's pinned report;
no target judgment or regeneration occurs. Target generation dates and independent nullable
profile/requirements/config/date stale flags preserve legacy unknowns.

Comparison configuration identity projects saved run settings onto scoring, optimizer,
layout, render, model/provider identity and current generation/parse/judge prompt versions.
Prices, storage/logging and other operational settings are excluded. Current scoring context
is explicit in output. Prompt identity is version-based; prompt changes must bump versions.
Metadata is local identity/provenance, not an authenticity guarantee.

Recurring signatures normalize case/Unicode/whitespace and unordered string collections
across every Requirement field except ID and importance. Only missing requirement instances
in at least two distinct selected folders form a recurring gap. All original instance IDs
and weights remain. Summed missing points have units of weighted percentage points per role;
they can exceed 100 across roles and are not effort estimates.

The factory-owned `runners.operation(user, "comparison")` encloses load and all coverage
stages. Its measured summary is returned after scope exit, without combining persisted JD
intake/generation usage. No comparison publication is created. CLI `--out DIR` alone exports
JSON and CSV; JSON stdout remains clean with notices/progress on stderr. Comparison holds
the normal user writer lock because model caches can change.

`DataService.reconfirm_fact_source(user, ref, *, excerpt=None) -> FactSource` is separate
from `reconfirm_evidence`. It resolves the owned old link and calls repository
`insert_fact_source` inside a transaction to revalidate the current record/document/quote.
Old stale links remain stale. The explicit CLI `--fact-source` flag chooses the link table;
there is no heuristic suffix dispatch.

`FileStore.runs_dir(user)` centralizes validation for discovery, resolution and creation.
Configured data-root symlinks are supported; users/user/subdirectory symlinks beneath the
resolved data root are rejected before access/creation. This is ordinary local path
validation, not a defense against an adversarial process racing directory replacements.
Repositories continue enforcing user ownership independently.

Run offline checks with `pytest`, `ruff check src tests`, `ruff format --check src tests`,
`mypy src/cvopt`, and `lint-imports`. Synthetic integration tests exercise actual intake,
review, EN/DE generation, discovery, comparison and export; historical fixture adapters are
explicitly synthetic and do not establish live provider semantic calibration. Do not read
personal output or `.env`, call live providers, or fabricate capture records for offline work.
