.PHONY: dev lint format test audit clean-bytecode clean

dev:
	uv sync --all-groups

lint:
	uv run ruff format --check
	uv run ruff check
	uv run ty check fleetroll/

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

audit:
	uv run pip-audit

clean-bytecode:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

clean: clean-bytecode
	rm -rf .coverage htmlcov/
	rm -rf dist/ build/ *.egg-info/
