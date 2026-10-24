Task: read a reference letter (often a German Arbeitszeugnis) and extract evidence for the person's claims.

The letter is in `<document>`; the person's known positions are in `<positions>`, each with a ref like `P1`.

Return:

- `issuer`: the employer that issued the letter. `author`: the signatory's name. `period_start`, `period_end`: the employment period as `YYYY-MM` ("" if not stated).
- `position_ref`: the ref of the listed position this letter is about (same employer, overlapping period). If none matches, `position_ref` is "" and `new_position` describes the position from the letter; otherwise fill `new_position` with "".
- `passages`: every evaluative passage, and every passage stating a concrete achievement or task.

Interpret the letter by meaning, not by its friendly surface. Reference letters in German-speaking countries use coded grading where faint praise is negative. Grade each passage:

| Typical wording (examples, not an exhaustive list) | grade |
| --- | --- |
| stets zu unserer vollsten Zufriedenheit; stets außerordentlich; übertraf unsere Erwartungen stets | very_good |
| stets zu unserer vollen Zufriedenheit; zu unserer vollsten Zufriedenheit; stets unsere volle Anerkennung | good |
| zu unserer vollen Zufriedenheit (without "stets"); stets zu unserer Zufriedenheit | satisfactory |
| zu unserer Zufriedenheit; im Grossen und Ganzen zu unserer Zufriedenheit; bemühte sich; war bestrebt | poor |
| a factual statement without evaluation (tasks, dates) | none |

- Swiss letters write "ss" for "ß"; treat them the same.
- Weigh omissions too: no word on conduct towards superiors, or a missing thank-you or good wishes, lowers the grade of the passage it concerns.
- A statement of what the person did takes the grade of the evaluation attached to it in the same or the next sentence.

Claims:

- Only `very_good` and `good` passages carry claims; every other passage has `claims` [].
- One claim per assertion the passage supports, phrased as a plain statement about the person's work ("Migrated the analytics data platform from MySQL to PostgreSQL, reducing query times by 40%"), in the letter's language, keeping every number, name and tool exactly.
- Type `achievement`, `experience` or `skill` as for a CV. `keywords`: tools, technologies and competences in canonical spelling, concepts in English ("PostgreSQL", "churn prediction", "people leadership"); at least one per claim.
- `excerpt`: a verbatim quote from the letter, at most 300 characters, that supports the passage's grade and claims. Copy it exactly; do not shorten words inside it.
