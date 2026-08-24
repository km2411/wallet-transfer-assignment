src := "src/wallet_transfer"

# Install dev dependencies
install:
    pip install -r requirements-dev.txt

# Lint (ruff + mypy + import-linter, once src/ exists)
lint:
    ruff check .
    @if [ -d {{src}} ]; then mypy src; else echo "mypy: {{src}} not created yet, skipping"; fi
    @if [ -d {{src}} ]; then lint-imports; else echo "import-linter: {{src}} not created yet, skipping"; fi

# Check formatting without modifying files
format-check:
    ruff format --check .

# Auto-format and fix lint issues
fmt:
    ruff format .
    ruff check --fix .

# Run the test suite
test:
    pytest

# Run tests with coverage, once src/ exists
test-cov:
    @if [ -d {{src}} ]; then pytest --cov=wallet_transfer --cov-report=term-missing; else echo "coverage: {{src}} not created yet, skipping"; fi

# Install the pre-commit git hook
pre-commit-install:
    pre-commit install

# Full CI gate: lint, format-check, test
ci: lint format-check test
