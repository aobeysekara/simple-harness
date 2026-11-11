# Fix a mistake and share the correction

A correction should explain what was wrong, why the fix works and where it applies. Accepting a finding doesn't, by itself, approve it for reuse elsewhere.

## Try the example

Start with the [walkthrough](reuse-demonstration.md) to compare SIMple with an updated Markdown handover. The command below runs the full example and writes handovers for later sessions:

```sh
uv run --locked python -m examples.reuse_loop --directory .simple/reuse-demo
```

The example uses made-up issue data and starts with a wrong effort calculation. It records a failed test, applies a fix for that project, runs the test again and approves the fix through a separate reviewer account. It then changes the recorded input version and checks that the advice is held back until it has been checked again.

The script performs the approvals; no human reviews the results. It saves tokens and reports under `.simple/`, which Git ignores. Use a new output directory each time.

## Record the correction

Call `post_update` with `correction` details and `goals` tags. MCP provides the full input schema. Include:

- The exact version of the work you're correcting, what was wrong and why you're changing it.
- Whether you're recording a fact or instructions. Instructions should refer to the facts they use, so project values don't have to be copied into several places.
- Where the correction applies, where it doesn't, and which facts you need to decide. A missing fact isn't a match.
- The entries it depends on and the tests it must pass. Identify the code, inputs and tests with references and file hashes.
- Whether you're proposing it for reuse.

Also list the correction's references in `derived_from`. SIMple rejects a new submission if one of those versions has changed. References inherited from an earlier revision still describe the old work; you must explicitly supply them again to confirm they apply to the new revision.

## Run the tests and approve reuse

Keep test code and inputs in a reviewed repository. The person running the local tests chooses a manifest: a file containing the case ID and relative paths for `code`, `inputs` and `test`.

```sh
simple --db DATABASE validate WORKSPACE CORRECTION_ID \
  --manifest case.json --evidence-directory .simple/runs --request-id check-1
```

Use a validator account. SIMple checks that the files match their recorded hashes, then runs the chosen checker with fixed arguments. Text retrieved from the harness can't choose arbitrary commands. Failed runs stay in the record. Posting `{"passed": true}` yourself doesn't count as a test run.

A different authorised reviewer must then approve the advice and where it can be used:

```sh
simple --db DATABASE review WORKSPACE CORRECTION_ID accepted \
  --revision REVISION --request-id approval-1 --approve-reuse
```

Test results are tied to the content that was checked. Changing only the review status doesn't make those results out of date. Changing the advice, where it applies or the versions it relies on requires further checks and review. Approval doesn't give anyone extra access to its sources.

## Ask for context

```sh
simple --db DATABASE open WORKSPACE TASK --request-id session-1
simple --db DATABASE context WORKSPACE TASK workload \
  --facts '{"project":"A","workflow":"triage","identity":"issue-entry","purpose":"analysis"}' \
  --byte-budget 16384
```

The response contains complete pieces of advice, the evidence behind them, exact references and the reasons they were included. SIMple matches the tags and facts you provide; it doesn't use semantic search.

Check the response status before using it. SIMple reports when it can't tell whether advice applies, when references are out of date, or when the size limit is too small. Ask for fresh context as the work progresses. It won't silently load the whole history to fill a gap.

Local test replay needs Unix file locking. Other CLI commands don't. You must trust the person running the tests and the checker they choose: running code in a separate process doesn't make untrusted code safe. A hash confirms which bytes were used, not whether their contents are true or all their sources have been named.
