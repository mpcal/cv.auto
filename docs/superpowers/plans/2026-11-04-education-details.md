# Education Details Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement these tasks inline.

**Goal:** Preserve all original education details by default, with a compact option.
**Architecture:** Extend the current structured education pipeline and review flow.
**Tech Stack:** Python, Pydantic, SQLite/Alembic, Typst, pytest.
**Spec:** `docs/superpowers/specs/2026-11-04-education-details-design.md`

## Global Constraints

- Default `[render] education_details = "all"`; alternative `"compact"`.
- Keep all facts in generated JSON and protect education from optimizer trimming.
- Preserve unrelated workspace edits; use synthetic fixtures for committed tests.

## Review Focus

- Legacy database migration preserves records with empty new fields.
- Refresh must not duplicate degrees or overwrite existing descriptive text.
- Hyperlink targets must survive PDF extraction and remain links in output PDFs.
- Compact mode must work during generation and standalone rendering.
- Long education must consume layout budget and remain intact on overflow.

### Task 1: Preserve and render education

Files: domain records/review/cv/render, extraction models/prompts/tasks, DB schema,
migration/repository/account export, ingestion, generation, render service/templates,
PDF extractor, configuration and corresponding pipeline tests.

- [x] Write failing PDF tests for full/compact mode and extraction of linked details;
  ingestion/storage and generation tests for summary, highlights, links and grade;
  migration and layout-budget regression tests.
- [x] Run those tests and verify failures describe the missing behavior.
- [x] Add `summary: str`, `highlights: list[str]`, and named `links` to education,
  propagate them through the pipeline, render both modes and reserve their space.
- [x] Run focused tests, fix regressions and validate configuration errors.

### Task 2: Refresh earlier imports and document the setting

Files: education CLI, ingestion service and review payload, extraction task recordings,
README and tests/pipeline/test_ingestion.py, test_cli_data.py.

- [x] Write failing tests that refreshing a stored CV proposes missing details,
  preserves existing values, and applying twice creates no duplicates.
- [x] Add `refresh_education(user, document_ref)` using the stored source, and
  `UpdateEducation` proposals that fill missing text and merge lists during review.
- [x] Expose `education refresh --document ID [--accept-all]` and manual editing
  of new fields; bump extraction prompt versions with synthetic offline fixtures.
- [x] Document the default/compact setting and refreshing old imports.
- [x] Run the entire offline suite, lint, format, types and import checks; review
  the diff and render a preview using the supplied source education.

## Verification record

Implemented and reviewed inline, with an independent code review. Review findings
for degree identity, structured list editing, DOCX links, manual link controls and
stale-proposal deduplication were reproduced in regression tests and corrected.

The full offline suite reports 152 passed, 1 failed, 5 deselected. The remaining
`tests/unit/test_text.py::test_split_roundtrip` failure for `['Aa vs.', 'Aa.']`
also reproduces using `HEAD:src/cvopt/domain/text.py`; those unrelated files were
left unchanged. Ruff lint/format, mypy and all five import contracts pass.

Local education-only previews using the supplied source text were rendered and
visually verified for both templates. Original user data and the existing run
remain untouched. Sending the private source to the configured model for a live
refresh was rejected by automatic approval review and requires explicit consent.
