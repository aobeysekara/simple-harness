# Build and check

The package is an experimental alpha under MIT. A version number does not imply a published package or production readiness.

From a clean checkout with Python 3.12:

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

The checks validate metadata, licence files, typing information, excluded local data, tests from the extracted source and a clean core-only installation. CI runs these checks without model credentials.

To install a locally built wheel:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install ./dist/simple_framework-0.1.0a1-py3-none-any.whl
.venv/bin/simple --help
```

The Python distribution remains named `simple-framework`; imports use `simple_framework`. The repository is named `simple-harness`. There is no verified PyPI install command.

Check the exact commit and CI result before sharing a build. Record its SHA-256 digest. Keep databases, credentials, transcripts and local reports out of releases.
