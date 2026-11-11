# SIMple Harness

Shared work for people and their agents.

SIMple is a Python library for sharing findings, decisions and handovers across people and AI sessions. It keeps a record of what was agreed, why it was agreed and which sources it relies on, so the next person or agent can continue the work.

You choose what to share. Your agents keep using their own tools and models, and SIMple holds the shared record. You can use it through Python, the command line or Model Context Protocol (MCP).

![Clients share selected work through the harness](docs/assets/harness.svg)

## When is it useful?

SIMple is for work that passes between people or agents over several sessions. A finding may need review, an assumption may change, or a new person may need to understand an earlier decision without reading every conversation.

For example, a team reviewing its backlog discovers that cancelled issues have been included in the workload estimate. Someone records the correction, the evidence for it and the projects it applies to. A reviewer approves it. Another agent can then use that correction in a later task, with the evidence attached.

If the recorded information behind the correction changes, SIMple stops offering it for reuse until it has been checked and approved again. This helps the team distinguish advice that is ready to use from advice that needs another look.

## Why use it instead of a shared document?

A shared Markdown file can hold the same information. SIMple adds checks that a document leaves to its readers and maintainers:

- **Review:** a correction needs passing tests and a different reviewer's approval before it can be reused.
- **Changes:** reusable advice is checked against the recorded versions of the work it depends on.
- **Access:** people and agents can only retrieve entries their source permissions allow them to read.
- **Handover:** a new session can ask for relevant work using a goal, known facts and a size limit.

A well-maintained document may be enough for your team. SIMple is worth trying when keeping those checks consistent has become difficult. We haven't yet shown that it saves time compared with Markdown.

## Try it

Install [uv](https://docs.astral.sh/uv/) and use Python 3.12:

```sh
git clone https://github.com/aobeysekara/simple-harness.git
cd simple-harness
uv sync --locked --python 3.12
uv run --locked python -m examples.reuse_comparison --directory .simple/first-comparison
```

The example follows the workload correction through review, a change to its recorded inputs, and another review. It also shows how someone keeping a Markdown handover up to date can reach the same result.

It uses made-up data and runs without a model account. The script performs the reviews and Markdown edits; no people or live agents take part. Use a new output directory each time.

The [walkthrough](docs/reuse-demonstration.md) explains the example, the files it creates and what the results mean. For a smaller example of saving and picking up work, run `uv run --locked python examples/reading_group.py`.

## Try separate clients

This script connects separate clients to the service, each with its own credentials:

```sh
uv sync --locked --extra server --python 3.12
uv run --locked --extra server simple-pilot
```

![Output from the scripted collaboration pilot](docs/assets/pilot.png)

This screenshot shows output from a real run of the script. The script checks the service; it doesn't involve live agents or human reviewers.

## Use it in your own work

Start a session, ask for the context you need, and share findings with references to their sources. Refresh that context as the work changes, then leave a handover for whoever continues it.

SIMple doesn't read your conversations automatically or decide which work to share. Context requests use the goals and facts you provide. A rule approved for one project doesn't automatically apply to another.

| I want to… | Guide |
|---|---|
| Use it from Python | [Python API](docs/python-api.md) |
| Connect clients | [Service and MCP setup](docs/shared-service.md) |
| Test and approve a correction | [Correction and reuse](docs/correction-reuse.md) |
| Understand the code and its limits | [Architecture](docs/architecture.md) |

## Limits and contributing

**Experimental alpha.** The API may change, and we haven't yet tested how easy it is for two people to use together. The core uses the Python standard library and SQLite. There is no published PyPI package or hosted service.

SIMple checks changes recorded in the harness; it doesn't watch outside files. It also cannot take back advice someone has already read, so clients need to refresh their context.

You must trust whoever runs the database. Clients use their own access tokens to connect through the service. SIMple checks permissions on the sources you name; it cannot detect sensitive information copied from a source you haven't named. Review records someone's judgement, and tests check specific cases. Neither guarantees that advice is always correct.

CI runs on Linux with Python 3.12. Local test replay needs Unix file locking; ordinary CLI commands don't. We haven't verified Windows support.

See [CONTRIBUTING.md](CONTRIBUTING.md) to suggest changes, [SECURITY.md](SECURITY.md) to report a security problem, and the [build guide](docs/alpha-release.md) to build a package locally.

MIT licensed. See [LICENSE](LICENSE).
