.PHONY: dev lint format test test-watch test-watch-coverage test-coverageaudit ci clean-bytecode clean

dev:
	uv sync --all-groups

lint:
	uv run ruff format --check
	uv run ruff check
	@echo "⚠️  Type checking (non-blocking):"
	@uv run ty check fleetroll/ || echo "Type checking found issues (not failing)"

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

test-watch:
	uv run ptw .

test-watch-coverage:
	uv run ptw . -- --cov=fleetroll --cov-report=html --cov-report=term-missing

test-coverage:
	uv run pytest --cov=fleetroll --cov-report=html --cov-report=term-missing

audit:
	uv run pip-audit

# Simulate CI pipeline locally
ci: lint test test-coverage audit
	@echo "✅ All CI checks passed!"

clean-bytecode:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

clean: clean-bytecode
	rm -rf .coverage htmlcov/
	rm -rf dist/ build/ *.egg-info/
