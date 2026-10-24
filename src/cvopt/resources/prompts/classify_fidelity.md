Task: classify how each CV statement relates to its source claims.

Each statement in `<statements>` has a ref, its text and its sources. Compare strictly:

- `unmodified`: the statement says what a source says, word for word or as a faithful translation.
- `rephrased`: the same meaning and scope in other words, or several sources merged without adding anything.
- `exaggerated`: stronger verbs, broader scope, more ownership, higher intensity or more importance than the sources support.
- `invented`: any assertion, tool, client, employer, result or number the sources do not contain.

When in doubt between two classes, choose the more altered one. Give one verdict per ref with a one-sentence `reason`.
