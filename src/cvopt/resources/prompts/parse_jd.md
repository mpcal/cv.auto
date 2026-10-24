Task: split a job description into the requirements an applicant tracking system would score.

The text is in `<job_description>`. It may be a web page with navigation, cookie notices or other noise; use only the job ad itself.

First decide `is_job_description`: true only if the text describes one job opening with requirements. A login page, an error page, a list of many jobs or an unrelated text is false; then return empty values for everything else.

Otherwise return the job `title`, the hiring `company`, the JD's `language` (ISO 639-1) and:

`requirements`, in the order the JD states them:

- One requirement per demanded qualification. "A and B" becomes two requirements; "A or B" stays one requirement whose keywords hold both alternatives.
- `text`: the requirement as the JD words it.
- `category`: hard_skill (tools, technologies, methods), soft_skill, experience (work done or led), domain (industry knowledge), years_experience (a number of years), education (a degree), certification, language.
- `importance`: must for required, must, essential, you have, you bring; nice for preferred, a plus, ideally, nice to have. Unmarked requirements are must.
- `keywords`: the JD's own terms for it, spelled exactly as the JD spells them. `synonyms`: common synonyms, acronyms, spelling variants and the German or English equivalent ("ML" for "machine learning", "Datenbanken" for "databases"). Never put the same term in both.
- `years`: for years_experience, the number of years (5 for "5+ years"), else 0. Its keywords name the skill the years are about.
- `language_code` and `language_level` (A1, A2, B1, B2, C1, C2 or native) for language requirements; "fluent" means C1, "business fluent" C1, "native" native, "good" B2, "basic" A2.
- `degree_level` (secondary, vocational, bachelor, master, doctorate) and `degree_fields` (the fields named) for education requirements; "university degree" without a level means bachelor.
- `certificate_names` for certification requirements.
- Leave unused fields as "", 0 or [].

`logistics`: work permit, location, travel, start date, salary and similar items, each as a short text. They are not requirements.

Benefits, company descriptions and tasks without a demanded qualification are not requirements.
