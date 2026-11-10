#!/usr/bin/env bash
set -euo pipefail

core_env=$(mktemp -d)
trap 'rm -rf "$core_env"' EXIT
uv export --locked --no-dev --no-emit-project --output-file "$core_env/requirements.txt" >/dev/null
uv venv --python 3.12 "$core_env/venv"
uv pip install --python "$core_env/venv/bin/python" --require-hashes -r "$core_env/requirements.txt"
package_version=$(uv run --locked python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
uv pip install --python "$core_env/venv/bin/python" --no-deps "dist/simple_framework-$package_version-py3-none-any.whl"
# Ignore caller Python path and optimisation settings when checking the installed wheel.
"$core_env/venv/bin/python" -I -c 'import importlib.util; assert all(importlib.util.find_spec(name) is None for name in ("mcp", "pydantic", "starlette"))'
"$core_env/venv/bin/python" -I -c 'from simple_framework import Administration, Harness, Repository, authenticate; import sys; assert not any(name in sys.modules for name in ("mcp", "pydantic", "starlette", "simple_framework.sqlite", "simple_framework.cli"))'
"$core_env/venv/bin/python" -I examples/reading_group.py > "$core_env/result.json"
"$core_env/venv/bin/simple" --help > /dev/null
"$core_env/venv/bin/python" -I -c 'import json,sys; data=json.load(open(sys.argv[1])); assert any(u["kind"] == "handover" for u in data["updates"]); print("PASS core-only wheel: collaboration, review, restart and CLI")' "$core_env/result.json"
uv run --locked --extra server python scripts/check-typing.py "$core_env/venv/bin/python"
