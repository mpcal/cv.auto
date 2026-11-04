# Preserve education details

The original CV's education section must survive ingestion, storage, generation and
PDF rendering. Preserve descriptive text (department, group, adviser), every bullet,
grades and named links. Education remains fixed factual content rather than a pool
of statements the optimizer can shorten or drop for relevance.

Use the existing Python/Pydantic, SQLite/Alembic and Typst pipeline. Add summary,
highlights and links to education records with empty defaults for existing records.
Expose `[render] education_details = "all" | "compact"`, defaulting to `"all"`.
Both templates and clean/annotated exports obey the setting. JSON retains all
facts regardless of presentation mode. Reserve room for the full details during
generation; a page-budget failure must not silently remove education.

Existing imports need an explicit `education refresh --document <id>` operation:
re-extract the stored source, propose missing education details through the existing
review flow, and preserve existing nonempty fields and deduplicate bullets/links.
No duplicate document, degree or unrelated claims should be created.

Approaches considered: a renderer-only change cannot recover facts discarded at
ingestion; storing the entire source section as one opaque string loses the current
structured degree/date handling. Extend the existing education record instead.
These implementation choices are the agent's calls based on the user's request.
