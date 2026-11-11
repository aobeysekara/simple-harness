# SIMple Harness

Shared work for people and their agents.

SIMple keeps findings, decisions and handovers in a durable workspace. Each contribution records who wrote it, the sources it uses and the exact revisions it depends on. Another session can pick up the work without copying a conversation.

Use it through Python, the command line or an optional Model Context Protocol (MCP) service. It works without a model provider. The core uses the Python standard library and SQLite.

![Clients share selected work through the harness](docs/assets/harness.svg)

**Experimental alpha.** APIs may change. Automated checks cover the collaboration rules; productivity gains and two-person usability are not established. A maintained Markdown file may be enough for a small team.

## See what changes when a dependency changes

A reviewed correction is reusable today. Its declared input version changes tomorrow.
SIMple withholds the correction and its dependent procedure from newly requested context
until their references, checks and review are brought up to date.

Run the synthetic example without a model account:

Install [uv](https://docs.astral.sh/uv/) and use Python 3.12:

```sh
git clone https://github.com/aobeysekara/simple-harness.git
cd simple-harness
uv sync --locked --python 3.12
uv run --locked python -m examples.reuse_comparison --directory .simple/first-comparison
```

The example starts with cancelled issues incorrectly counted as workload. It records a
failed check, fixes the calculation, and approves a scoped correction and a dependent
procedure using a separate reviewer identity. It then changes the declared contract version.

| Stage | SIMple: eligible items | Maintained Markdown: allowed items | Unchanged snapshot: items still shown |
|---|---:|---:|---:|
| Checked and approved | 2 | 2 | 2 |
| Dependency changed | 0 | 0 | 2 |
| Correction replayed, not yet reapproved | 0 | 0 | 2 |
| Both items revalidated and reapproved | 2 | 2 | 2 |

**Maintained Markdown can reach the same safe result.** Here, the script acts as its
maintainer and explicitly withdraws outdated guidance. SIMple enforces eligibility when
context is requested. The unchanged snapshot is a separate control, not the Markdown baseline.

Inspect the handovers in `.simple/first-comparison/comparison/` and the observed context
packets in `operator/`. Use a new output directory for each run. The review and Markdown
maintenance are scripted; this is not a live agent benchmark. The change is a declared
version bump, not a change in calculation semantics. SIMple does not watch external files
or recall context already read.

See the [walkthrough and trial protocol](docs/reuse-demonstration.md) for the evidence,
limits and an unaided two-person evaluation. For the smaller persistence example, run
`uv run --locked python examples/reading_group.py`.

## Connect separate clients

To exercise separate authenticated service clients:

```sh
uv sync --locked --extra server --python 3.12
uv run --locked --extra server simple-pilot
```

![Output from the scripted collaboration pilot](docs/assets/pilot.png)

Screenshot of locally rendered output from a real scripted run. These are protocol checks, not live agent or human acceptance results.

## How it works

1. Open an owned session with `open_session`.
2. Retrieve permitted context using an explicit goal, known facts and a byte budget.
3. Post selected work with source IDs and exact dependency revisions.
4. Refresh context at checkpoints and leave a handover when finished.

Opening a session does not load workspace history. When a declared dependency changes, affected guidance needs reconsideration. A new submission with an outdated reference conflicts. An identical committed retry keeps its original result, subject to current access.

Corrections can become reusable guidance after trusted local checks pass and a different reviewer approves both content and scope. Missing applicability facts remain unknown. A project-specific rule does not become a default for every project.

| Start here | Guide |
|---|---|
| Embed the library | [Python API](docs/python-api.md) |
| Connect clients | [Service and MCP setup](docs/shared-service.md) |
| Test and approve a correction | [Correction and reuse](docs/correction-reuse.md) |
| Understand the boundaries | [Architecture](docs/architecture.md) |

## Limits and contributing

The database operator is trusted. Independent clients use separate bearer credentials through the service. Source restrictions apply to declared references; they cannot detect undisclosed sensitive paraphrases. Review records a judgement, and replay checks particular cases. Neither proves universal correctness.

Python 3.12 on Linux is the CI target. Local replay needs Unix file locking; ordinary CLI commands do not. Windows execution is not verified. The repository does not publish a PyPI package or operate a hosted service.

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and pull requests, [SECURITY.md](SECURITY.md) for reporting, and the [build guide](docs/alpha-release.md) for local distributions.

MIT licensed. See [LICENSE](LICENSE).
