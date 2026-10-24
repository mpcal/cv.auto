Task: decide whether a new claim states the same assertion as any of the existing claims.

The new claim is in `<new_claim>`, the existing claims in `<existing_claims>`, each with a ref like `C1`. The claims may be in different languages.

Give exactly one verdict per existing claim:

- `same` is true only when both claims assert the same thing with the same scope: the same work, result or skill. A claim about a related but different task, tool or result is not the same.
- When the assertion is the same but a number differs (a percentage, a count, a duration), `same` is true and `quantity_conflict` is true. Otherwise `quantity_conflict` is false.
- `reason`: one short sentence.
