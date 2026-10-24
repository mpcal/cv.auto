Task: judge, for each CV element, whether it shows that the person meets one job requirement.

The requirement is in `<requirement>`, the CV elements in `<cv_elements>`, each with a ref like `E1`. Judge each element on its own, never in combination with others.

- `semantic`: the element states the same competence, experience or qualification as the requirement, possibly in other words or another language. "Built churn models in scikit-learn" meets "machine learning experience".
- `partial`: related but weaker: an adjacent tool (MySQL for PostgreSQL), lower seniority (member of a team for "led teams"), narrower scope, or fewer years.
- `none`: no relation. Sharing a generic word ("data", "team", "experience") is none.

A requirement word appearing in the element is not enough: the meaning must fit ("Java" in "JavaScript" is none). Give exactly one verdict per element, with a one-sentence `reason`.
