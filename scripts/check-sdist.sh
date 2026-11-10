#!/usr/bin/env bash
set -euo pipefail

sdist_env=$(mktemp -d)
trap 'rm -rf "$sdist_env"' EXIT
package_version=$(uv run --locked --extra server python - <<'PY'
import tomllib

print(tomllib.loads(open("pyproject.toml").read())["project"]["version"])
PY
)
tar -xzf "dist/simple_framework-$package_version.tar.gz" -C "$sdist_env"
cd "$sdist_env/simple_framework-$package_version"
unset PYTHONPATH PYTHONHOME UV_PROJECT_ENVIRONMENT
uv sync --locked --extra server --python 3.12
uv run --locked --extra server python -I - <<'PY'
from pathlib import Path

import simple_framework

assert Path(simple_framework.__file__).resolve().is_relative_to(Path.cwd() / "src")
assert not Path("docs/evidence").exists()
print("PASS imports use extracted source; full evidence is excluded")
PY
uv run --locked --extra server python -I -m pytest --tb=short
