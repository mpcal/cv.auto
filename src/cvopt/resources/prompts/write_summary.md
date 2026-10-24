Task: write a CV headline and summary sentences from statements already selected for the CV.

The statements are in `<statements>` with refs S1, S2, …; the job's requirements in `<requirements>`, with the JD's terms.

Write, in the output language:

- `headlines`: three alternatives, each at most 8 words: a professional title plus focus, in the JD's terms where the statements support them (e.g. "Lead Data Scientist for Retail Demand Forecasting"). No lists of tools. At least two must be `rephrased`: a title and focus the statements literally support, without "senior", "expert" or similar upgrades.
- `sentences`: six summary sentences. At least four must be `rephrased`: they stay strictly within what their statements say, with no stronger verbs, no broader scope and no added qualities ("experienced", "expert", "proven"). At most two may be `exaggerated`.
  Each sentence condenses what two or more statements show together into one claim about the person's profile ("Data scientist who leads forecasting teams and brings models into production with MLflow and Kubernetes."). Never repeat a single statement's wording; the summary must read as a profile, not as a copy of the bullets.

Rules:

- Each item's `source_refs` names every statement it is built from; never add facts they do not contain.
- Keep numbers, names and tools exactly as in the statements; prefer no numbers at all in the summary.
- `fidelity`: `rephrased` if the item stays within what its statements say, `exaggerated` if it uses stronger verbs or broader scope.
- Full confidence, no hedging, no first person ("I").
