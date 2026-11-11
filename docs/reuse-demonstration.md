# When shared guidance becomes outdated

The useful distinction is enforcement. A maintained Markdown handover can record the
same decisions, references and evidence. SIMple checks whether declared guidance is
eligible at retrieval time, including its dependencies, review, scope and permissions.
That is a mechanism, not yet evidence of better productivity.

## Reproduce the comparison

From the repository root, with Python 3.12 and uv:

```sh
uv sync --locked --python 3.12
uv run --locked python -m examples.reuse_comparison --directory .simple/reuse-comparison
```

Use a new directory each time. The command needs no server extras or model credentials.
Local replay requires Unix file locking; Windows is not verified. All data is synthetic.
The command creates credentials in its ignored `operator/` directory. Do not share that
directory or the database; inspect and redact reports before sharing them.

The fixture corrects cancelled issues being counted as workload and raw row IDs being
treated as stable. Its two reusable items are a scoped correction and a procedure
depending on that correction. The faulty implementation fails the checked-in evaluator;
the corrected implementation passes. A separate reviewer identity approves reuse.

The script then bumps the declared contract version without changing calculation
semantics. This deliberately isolates dependency freshness: even a change that later
proves harmless requires reconsideration. It does not test whether a model can discover
a new business rule or recognise an external file change.

## Read the evidence

Paths below are relative to the output directory:

| Stage | Observed SIMple packet | Scripted maintained handover | Expected eligible / allowed items |
|---|---|---|---|
| Approved | `operator/before-contract-change.json` | `comparison/01-approved.md` | 2 / 2 |
| Dependency changed | `operator/stale.json` | `comparison/02-dependency-changed.md` | 0 / 0 |
| Correction replayed, review pending | `operator/awaiting-review.json` | `comparison/03-replayed-not-approved.md` | 0 / 0 |
| Both items revalidated and reviewed | `operator/packet.json` | `comparison/04-reapproved.md` | 2 / 2 |

The stale packet names both affected items with `needs_revalidation`. The pending-review
packet offers neither item: passing the correction's replay alone does not restore reuse.
The final packet is retrieved after reopening storage. Failed replay records remain in
`operator/runs/`; the demo also checks unknown scope and a restricted-source canary.

`comparison/results.json` records the observed counts and the scripted Markdown decisions.
Initial and final Markdown content is exported from the same permitted context, including
scope, evidence and exact references. At the known change point the script explicitly
withdraws both items from the maintained handover. This is a model of correct maintenance,
not an independent Markdown validator or a measured human workflow.

`comparison/unchanged-snapshot.md` preserves the original handover. Its two items remain
visible, but are outdated after the change. It is a separate control; do not present it
as a fair maintained-Markdown competitor. SIMple also cannot retract an old context
packet already fetched by a consumer. Clients must refresh at checkpoints.

## What this establishes

- The harness withholds declared outdated guidance, including a dependent procedure.
- Replay and review are separate gates; both must be satisfied for reuse.
- Correctly maintained Markdown can communicate the same safe decision.

It does not establish agent accuracy, reduced context consumption, saved coordination
time, external usability or superiority over a disciplined Markdown workflow. The
scripted reviewers are identities controlled by one process, not independent people.

## Unaided trial with two people

This trial remains unrun. A maintainer and another engineer should use the public
checkout without private project history. Record the commit and Python version first.

1. Give the engineer only the README. Ask them to run the example and explain why each
   item disappears and what restores it. Record setup failures, time to first successful
   run and every clarification requested; do not count reading this scripted example as
   an independent modelling result.
2. For a separate workflow comparison, agree a new consequential correction and evaluator
   before starting. Use an unfamiliar synthetic case, such as warehouse-local event
   times versus carrier cut-offs. Keep raw data, task interfaces, evidence and review
   criteria identical in both conditions. Do not reuse this fixture as a blind test.
3. Supply the reviewed correction through SIMple in one condition and a maintained
   Markdown handover in the other. The Markdown maintainer may edit, version and link
   evidence normally. Give both maintainers the same dependency-change notification.
   Ask fresh consumers to continue after that change; allow both to pause for review.
4. Counterbalance condition order across participants or use matched cases to reduce
   learning effects. Record stale guidance used, correct outputs, evidence citations,
   clarification requests and maintenance actions. Separate maintainer effort from
   consumer effort. Measure context bytes only if all reads can be captured in both
   conditions; otherwise report that metric as unavailable.
5. Preserve failures and report individual runs and limitations. If both conditions are
   equally safe and convenient, report that. Do not promote a one-case result into a
   general productivity claim.

Use the [client workflow](../examples/clients/WORKFLOW.md) for MCP connections and the
[correction guide](correction-reuse.md) for replay and review. A trial report should
identify the task, conditions, exact versions, results, manual interventions and what
was not measured. Never publish credentials or unreviewed conversation transcripts.
