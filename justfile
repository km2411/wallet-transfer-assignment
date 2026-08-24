src := "src/wallet_transfer"
flyway_image := "flyway/flyway:10-alpine"

# Install dev dependencies
install:
    pip install -r requirements-dev.txt

# Apply schema migrations via Flyway against the docker-compose Postgres.
# SEED_DEMO_DATA=true also applies db/seed/ (ADR-0010) — unset by default everywhere,
# including CI and testcontainers-backed tests.
migrate:
    #!/usr/bin/env bash
    set -euo pipefail
    locations="filesystem:/flyway/sql"
    volumes=(-v "{{justfile_directory()}}/db/migrations:/flyway/sql:ro")
    if [ "${SEED_DEMO_DATA:-}" = "true" ]; then
        locations="${locations},filesystem:/flyway/sql_seed"
        volumes+=(-v "{{justfile_directory()}}/db/seed:/flyway/sql_seed:ro")
    fi
    docker run --rm --network wallet_transfer_net "${volumes[@]}" {{flyway_image}} \
        -url=jdbc:postgresql://postgres:5432/wallet_transfer \
        -user=wallet -password=wallet \
        -connectRetries=10 \
        "-locations=${locations}" \
        migrate

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
