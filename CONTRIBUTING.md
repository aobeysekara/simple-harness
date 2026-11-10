# Contributing

Small fixes, clear bug reports and feedback from actual collaborative work are welcome. Discuss larger changes in an issue first.

## Check a change

Use Python 3.12 and uv:

```sh
uv sync --locked --extra server --python 3.12
make test
make lint
uv run --locked --extra server simple-pilot
uv build
uv run --locked python scripts/check-dist.py
bash scripts/check-sdist.sh
bash scripts/check-core.sh
```

These checks use synthetic data and make no paid model calls. The pilot starts a temporary loopback service and stops it afterwards.

Keep the application core independent of storage and transport adapters. Preserve access checks, source restrictions, exact revisions, review authority and retry behaviour. Add a focused regression for a bug fix. Keep fixtures small and self-contained so tests work from the source distribution.

Open a pull request against `main`. Describe the problem, resulting behaviour, relevant commands and known limits. Keep unrelated changes separate. Passing CI does not replace review.

Contributions require approval from `@aobeysekara`, passing quality checks and resolved review conversations. New commits dismiss earlier approvals. The repository owner retains direct push access.

## Reports and rights

For bugs, include the commit, Python version, minimal reproducer, expected behaviour and actual result. Remove credentials, personal data, private URLs and database exports. Use [SECURITY.md](SECURITY.md) for vulnerabilities.

Contribute only material you have the right to share under the MIT licence. Preserve any required third-party notices. Discuss code and evidence respectfully.
