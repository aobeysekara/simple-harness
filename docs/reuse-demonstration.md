# What happens when shared advice goes out of date?

You can keep decisions, references and test results in Markdown. Someone then has to keep that handover up to date when the work changes.

SIMple checks those rules when you ask for context. Has the advice been approved? Do its references still point to the current versions? Does it apply to this task, and can you read its sources? This example shows those checks working. It doesn't tell us yet whether they save people time.

## Run the example

From the repository root, with Python 3.12 and uv:

```sh
uv sync --locked --python 3.12
uv run --locked python -m examples.reuse_comparison --directory .simple/reuse-comparison
```

Use a new output directory each time. You don't need the server extras or a model account. The example runs local tests using Unix file locking; we haven't verified it on Windows.

All the data is made up. The script saves access tokens under `operator/` in the output directory, which Git ignores. Don't share that directory or its database. Check reports for anything private before sharing them.

## What the script does

The example has two mistakes: it counts cancelled issues as work, and it assumes row IDs stay the same between files. It records two pieces of advice to fix them: a rule for this project and instructions that depend on that rule.

The original calculation fails the test. The fixed calculation passes. The script then uses a separate reviewer account to approve the advice for reuse.

Next, it changes the recorded version of the input contract—the rules for interpreting the data. The calculation rules themselves stay the same. This lets us check what happens when a reference changes, even if the advice turns out to be valid after another review. We aren't testing whether a model can spot a new business rule or notice a change to an outside file.

## Find the results

These paths are inside the output directory. A context packet is the JSON response containing the advice SIMple allows you to use.

| Step | SIMple's response | Updated Markdown handover | Items allowed by each |
|---|---|---|---|
| Approved | `operator/before-contract-change.json` | `comparison/01-approved.md` | 2 |
| Contract version changed | `operator/stale.json` | `comparison/02-dependency-changed.md` | 0 |
| Fix tested again, review pending | `operator/awaiting-review.json` | `comparison/03-replayed-not-approved.md` | 0 |
| Both items checked and approved again | `operator/packet.json` | `comparison/04-reapproved.md` | 2 |

In `stale.json`, both items are marked `needs_revalidation`. Neither is offered for reuse. Running the correction's test again isn't enough: the next response still leaves both items out while approval is pending. The final response contains both items after the script has checked and approved them again and reopened the database.

Failed test runs stay in `operator/runs/`. The example also checks that SIMple leaves advice out when it doesn't know where it applies, and doesn't reveal a test entry whose source the reader cannot access.

### How the Markdown comparison works

`comparison/results.json` records the counts from SIMple and the choices made by the script acting as the Markdown maintainer.

The first and last handovers contain the same advice, limits, evidence and references as SIMple's responses. When the contract version changes, the script replaces the current handover with a warning not to use either item. That represents someone keeping Markdown up to date. It isn't an automatic Markdown checker, and we haven't measured how long a person would take to do it.

`comparison/unchanged-snapshot.md` keeps the original handover. It still shows the two outdated items. Keep this separate from the updated Markdown result: leaving a file untouched isn't a fair comparison with someone maintaining it properly.

An old SIMple response can go out of date too. SIMple cannot take back advice that someone has already read. Clients need to request fresh context as the work progresses.

## What we can say so far

SIMple leaves outdated advice out of new responses, including instructions that depend on it. Tests and review must both be complete before the advice can be reused. A properly updated Markdown handover can tell its reader to make the same safe choice.

We haven't shown that SIMple improves agent accuracy, uses less context, saves time or is easier to use than Markdown. One script controls all the reviewer accounts in this example. They aren't independent people.

## Try it with another engineer

We haven't run this trial yet. Use the public repository and record the commit and Python version before starting.

1. **Check the instructions.** Give another engineer just the README. Ask them to run the example and explain why the advice disappears and what brings it back. Note setup problems, time to the first successful run and any questions they need answered. This checks the instructions, not their ability to solve a modelling problem.
2. **Choose a new problem for the comparison.** Agree the correction and how you'll check the answer before you start. Use unfamiliar, made-up data with a mistake that matters—for example, warehouse event times and carrier cut-offs recorded in different time zones. Give both approaches the same data, task instructions, evidence and review requirements. Don't use the example they have just seen as a blind test.
3. **Run both approaches fairly.** Supply the reviewed correction through SIMple in one run and an updated Markdown handover in the other. Let the Markdown maintainer edit, version and link evidence as usual. Tell both maintainers about the same input change. Ask people or agents starting fresh to continue the work, and let either approach pause for review.
4. **Record what people actually do.** Note wrong answers, outdated advice used, evidence cited, questions asked and updates needed. Record the maintainer's effort separately from the reader's. Vary which approach people try first, or use similar but different tasks, so the second run doesn't simply benefit from practice. Only compare context size if you can capture everything read in both runs.
5. **Report the failures too.** If both approaches work equally well, say so. Include the results from each run and what you didn't measure. One example isn't enough to claim a general improvement in productivity.

The [client guide](../examples/clients/WORKFLOW.md) covers MCP connections. The [correction guide](correction-reuse.md) covers tests and review. In your trial report, include the task, versions, results and any help people needed. Keep access tokens and private conversations out of it.
