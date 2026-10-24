Task: propose CV statements for a job requirement that none of the person's confirmed claims covers.

The requirement is in `<requirement>`, the person's positions in `<positions>` and confirmed claims in `<claims>`.

Write, in the output language, at most three candidates:

- For a skill-like requirement (a tool, method or competence): one `skill` candidate holding just the keyword in the JD's spelling, and one `highlight` candidate that shows the skill in use at a plausible position.
- For an experience or domain requirement: one or two `highlight` candidates.
- Place a highlight under the most plausible position: the most recent one whose role and domain make the statement credible, else the most recent position. Give its ref in `position_ref`; skills have `position_ref` "".

Rules:

- No figures at all: no numbers, percentages, counts or years.
- Do not name employers, clients or products that are not in the data.
- Plausible for the person and the position, phrased with full confidence.
- `inferable` is true only if the statement is a close substitute of a confirmed skill (same category, transferable, e.g. C++ → Rust) or a natural extension of confirmed experience at a similar level; then `inferred_from` names those claim refs. Otherwise `inferable` is false and `inferred_from` is [].
