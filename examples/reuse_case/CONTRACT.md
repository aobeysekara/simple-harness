# Issue-triage fixture

This synthetic example totals effort points by issue category within one project and workflow. It is a test of explicit policy and record identity.

Project A excludes cancelled issues. Project B includes them. Every call supplies its project policy; one project's rule must not become another's default.

The declared record identity contains project, workflow, issue ID and entry ID. Repeated identical entries count once. Conflicting entries fail. Export-local row IDs are not stable between extracts.

`cases.json` fixes ten expected outcomes, including reordered rows, missing scope or identity, and conflicting records. `faulty.py` deliberately includes cancelled issues. `model.py` applies the supplied policy. `faulty_replay.json` records the expected failed checks.

The trusted checker compares candidate output in a separate process. This is not an operating-system security sandbox. Consumers receive the interface document and their own input, not the expected results.
