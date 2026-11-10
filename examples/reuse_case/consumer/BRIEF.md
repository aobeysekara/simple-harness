Reconcile triage workload for project A using `extract.json`.

Follow the interface in `API.md`. Return one integer workload quantity per TYPE, sorted by TYPE. Use `transform(rows, contract)` as the entry point.

Use the supplied context to establish the project's workflow policy and record identity before implementing. Write `model.py`, focused tests and a short result with the references you used.

If the supplied information does not establish the project's workflow policy or line identity, stop and state what information is missing. Do not invent missing facts.

Any review approval in this synthetic exercise is scripted using a separate identity. It is not independent human verification.
