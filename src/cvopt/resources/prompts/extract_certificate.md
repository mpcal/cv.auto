Task: extract one record from a diploma or certificate.

The document is in `<document>`. Decide whether it is an education record (a degree, diploma or school leaving certificate) or a certificate (a professional certification or course certificate), and fill that record; fill the other with "".

- Education: institution, area (field of study), degree type as written, degree level (secondary, vocational, bachelor, master, doctorate, other), start and end months, grade as written.
- Certificate: name, issuer, issue month, URL if printed.
- Months as `YYYY-MM`; a year alone becomes `YYYY-01`.
- `claims`: skill claims only for competences the document explicitly attests (named courses, modules or certified skills). The text names the competence; `keywords` give it in canonical spelling. Do not infer skills from the degree's title alone.
