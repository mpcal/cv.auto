Task: extract structured data from a CV.

The CV is in `<document>`. Return:

- `profile`: name, email, phone, city, country, website and online profiles (network name and URL) as written.
- `positions`: every job, in the order the CV lists them, with employer, employer location, title and months.
- `education`, `languages`, `certificates`: every entry.
- `claims`: the assertions the CV makes about the person.

Claims:

- One claim per assertion, in the CV's own wording. Split a bullet that asserts two things into two claims; do not merge bullets.
- Type `achievement` for a result or outcome (often with a figure), `experience` for work done or responsibility held, `skill` for a tool, technology, method or competence.
- One `skill` claim per tool or competence in a skills list ("Python", "stakeholder management"); its text is the skill's name.
- `keywords`: the tools, technologies and competences the claim mentions, in canonical spelling ("PostgreSQL", "scikit-learn", "people leadership"). Every claim has at least one keyword.
- `position`: the index (0-based) in `positions` of the job the claim belongs to, or -1 for claims not tied to a job (skills lists, summaries).
- Keep numbers, names and tools exactly as written. Do not add, infer or generalize.

Formats:

- Months as `YYYY-MM`; a year alone becomes `YYYY-01`. A current job ("present", "today", "heute") has `end_month` "".
- `degree_level`: secondary, vocational, bachelor, master, doctorate, or other. BSc/BA/BEng are bachelor; MSc/MA/MBA/Diplom are master; PhD/Dr. are doctorate.
- `language_code`: lower-case ISO 639-1 ("de" for German or Deutsch). `level`: one of A1, A2, B1, B2, C1, C2, native. Map fluency words: native/mother tongue → native; fluent/proficient → C1 unless a CEFR level is given; intermediate → B1; basic → A2. An explicit CEFR level in the CV wins.
- Absent values are "" (or [] for lists). Extract nothing that is not in the text.
