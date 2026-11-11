# Correct, check and reuse

A useful correction needs a reason, evidence and a clear scope. Ordinary acceptance does not turn a finding into reusable guidance.

## Try the example

For a compact before/after comparison with maintained Markdown, start with the
[reuse demonstration](reuse-demonstration.md). The command below exposes the full
underlying loop and prepares consumer handovers.

```sh
uv run --locked python -m examples.reuse_loop --directory .simple/reuse-demo
```

The synthetic issue-triage example starts with a faulty effort calculation. It records a failed check, applies a project-specific correction, replays the case and obtains approval through a separate reviewer identity. It then changes the declared input version and checks that guidance needs revalidation before reuse.

Approval in this example is scripted, not human verification. Generated token files and reports stay under `.simple/`, which Git ignores. Use a new output directory each time.

## Record the correction

Use `post_update` with optional `correction` metadata and exact `goals` tags. MCP exposes the full schema. The metadata identifies:

- The exact work being corrected, the mistake and rationale.
- Knowledge or procedure, with procedures referring to facts rather than copying project-specific values.
- Scope, exclusions and required facts. Missing facts remain unknown.
- Exact dependencies and acceptance cases identifying code, inputs and tests by reference and digest.
- An explicit proposal for reuse.

The correction's references must also be declared in `derived_from`. A changed dependency conflicts on a new submission. Inherited references remain historical until explicitly reasserted.

## Replay and approve

Keep test code and inputs in a reviewed repository. The trusted local operator selects a manifest containing a case ID and relative paths for `code`, `inputs` and `test`.

```sh
simple --db DATABASE validate WORKSPACE CORRECTION_ID \
  --manifest case.json --evidence-directory .simple/runs --request-id check-1
```

Use a validator credential for replay. The adapter checks declared bytes and invokes the selected checker with fixed arguments. Retrieved text cannot supply arbitrary commands. Failures remain recorded; posting `{"passed": true}` does not create trusted evidence.

A different authorised reviewer then approves both content and scope:

```sh
simple --db DATABASE review WORKSPACE CORRECTION_ID accepted \
  --revision REVISION --request-id approval-1 --approve-reuse
```

Validation binds semantic content, so review-only revisions do not invalidate the tested content. Content, scope or declared version changes require appropriate replay and review. Approval never widens source permissions.

## Retrieve context

```sh
simple --db DATABASE open WORKSPACE TASK --request-id session-1
simple --db DATABASE context WORKSPACE TASK workload \
  --facts '{"project":"A","workflow":"triage","identity":"issue-entry","purpose":"analysis"}' \
  --byte-budget 16384
```

The packet includes complete guidance, supporting evidence, exact references and selection reasons. It uses explicit tags and facts, not semantic search. Check its status before use. Unknown applicability, stale dependencies and insufficient budget remain explicit. Refresh at checkpoints; there is no automatic history fallback.

Replay requires Unix file locking. Other CLI commands work without that module. The operator and selected checker remain trusted, and subprocess separation is not a hostile-code sandbox. Hashes identify bytes, not truth or complete information flow.
