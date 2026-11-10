.PHONY: test lint

test:
	uv run --locked --extra server pytest

lint:
	uv run --locked --extra server ruff check .
	uv run --locked --extra server ruff format --check .
	uv run --locked --extra server mypy
