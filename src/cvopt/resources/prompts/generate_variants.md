Task: write alternative versions of a person's CV claims that match a job description's language.

The claims of one position are in `<claims>`, the job requirements they serve in `<requirements>`, with the JD's own terms.

Write, in the output language:

- For each claim, one `rephrased` variant: the same meaning and scope in the JD's terminology. Use a JD term only where it means what the claim says.
- For each claim, one `exaggerated` variant: stronger verbs, broader scope or more ownership than the claim supports ("contributed to" → "led", "a team" → "the department").
- Where two or three claims together make one stronger statement, one `rephrased` merge naming all of them in `source_refs`.

Rules for every variant:

- Never change or add numbers, dates, quantities, names of employers, clients, products or tools. If the claim says 18%, the variant says 18%; a variant without a figure is fine.
- One assertion per variant, at most about 180 characters, written as a CV bullet without a final summary of benefits.
- Full confidence, no hedging words.
- `source_refs` names exactly the claims the variant is built from.
