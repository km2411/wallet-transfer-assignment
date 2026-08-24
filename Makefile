.PHONY: install lint format-check fmt test test-cov ci pre-commit-install

SRC := src/wallet_transfer

install:
	pip install -r requirements-dev.txt

lint:
	ruff check .
	@if [ -d $(SRC) ]; then mypy src; else echo "mypy: $(SRC) not created yet, skipping"; fi
	@if [ -d $(SRC) ]; then lint-imports; else echo "import-linter: $(SRC) not created yet, skipping"; fi

format-check:
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

test:
	pytest

test-cov:
	@if [ -d $(SRC) ]; then pytest --cov=wallet_transfer --cov-report=term-missing; else echo "coverage: $(SRC) not created yet, skipping"; fi

pre-commit-install:
	pre-commit install

ci: lint format-check test
