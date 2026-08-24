---
kind: adr
status: proposed
owner: Kartik Mittal
last_reviewed: 2026-08-24
---

# ADR-0004 — Schema design with DB-level invariants, versioned via Flyway

- **Status:** Proposed
- **Deciders:** Kartik Mittal (candidate)

## Context

The schema needs to make several invalid states unrepresentable at the database level rather than
rely on application discipline — the evaluation guide asks directly whether a ledger row can
exist without a transfer, and whether a duplicate transfer can happen by accident. Separately,
schema changes need a version-controlled, reproducible way to reach every environment (local dev,
automated tests, and — designing with eventual production use in mind, per explicit direction —
a real deploy pipeline), and the migration tool should be usable the same way regardless of which
language a given service is written in, since it's a company-wide concern, not a Python-specific
one.

## Decision drivers

- Constraints (uniqueness, foreign keys, checks) belong in the schema, not just in service-layer
  `if` statements — the DB should refuse an invalid state even if application code has a bug.
- One schema source of truth shared by local dev, CI, and the automated test suite — no separate,
  divergent "test-only" schema-creation path that can drift from what actually gets deployed.
- Explicit direction: design with Flyway in mind for migration/version control, independent of
  the fact that the service itself is Python.

## Considered options for migration tooling

1. **Alembic.** The more "native" choice for a Python/SQLAlchemy stack — autogenerates migrations
   by diffing ORM models. Rejected per explicit direction, and it ties schema evolution to an
   ORM's model-diffing rather than hand-authored, directly reviewable SQL.
2. **Hand-rolled SQL scripts with a custom runner.** Rejected: re-implements what a real migration
   tool already does correctly — ordering, checksum validation against already-applied
   migrations, detecting an out-of-order or modified-after-the-fact migration.
3. **Flyway** — versioned, hand-authored SQL migration files, tracked via its own
   `flyway_schema_history` table, run via the official `flyway/flyway` Docker image (no local JVM
   needed). *Chosen*, per explicit direction — also a natural fit for a system designed with
   eventual production use in mind, since it's language-agnostic and not tied to this service's
   Python runtime.

## Decision

**Migrations.** Live under `db/migrations/`, one forward-only file per change, Flyway's own
naming convention: `V<version>__<description>.sql` (e.g. `V1__create_wallets.sql`,
`V2__create_transfers.sql`, `V3__create_ledger_entries.sql`,
`V4__create_idempotency_records.sql`). No down-migrations — a mistake is fixed forward with a new
versioned file, consistent with Flyway's community-edition model and, separately, the safer
default for a system holding real financial data (a mechanical rollback against live ledger data
is rarely actually safe to run). Applied via the Flyway CLI, never from application code —
migrations are a deploy/setup-time concern, not part of the service's runtime path. Locally, via
a `make migrate` target against the `docker-compose.yml` Postgres; the `testcontainers`-backed
Postgres used by the automated test suite gets the same migrations applied the same way before
tests run, so there is exactly one schema source of truth.

**Primary keys are `UUID` (via `gen_random_uuid()`), not sequential integers**, across every
table — deliberate, not a leftover placeholder: a financial API exposing sequential IDs
(`/transfers/1042`) leaks volume and lets a caller enumerate other parties' transfers by guessing
adjacent IDs; UUIDs also generate correctly from any node without a shared sequence, which matters
for a schema designed with eventual production/horizontal deployment in mind (ADR-0001, ADR-0003).
The tradeoff — larger index footprint than `BIGSERIAL`, no natural insertion order — is accepted;
this system's scale doesn't make that cost meaningful, and `created_at` covers ordering needs.

**Schema** (initial cut — refined per table as each is implemented):

- `wallets(id UUID PK, balance BIGINT NOT NULL CHECK (balance >= 0), created_at, updated_at)` —
  minor units (e.g. cents), never `NUMERIC`/float, consistent with the project's money-handling
  rule. The `CHECK` makes a negative balance unrepresentable regardless of what application code
  does.
- `transfers(id UUID PK, from_wallet_id UUID FK -> wallets, to_wallet_id UUID FK -> wallets,
  amount BIGINT NOT NULL CHECK (amount > 0), status TEXT NOT NULL CHECK (status IN
  ('PENDING','PROCESSED','FAILED')), failure_reason TEXT, created_at, updated_at, CHECK
  (from_wallet_id <> to_wallet_id))` — the last check rejects self-transfers at the DB level, as
  defense-in-depth behind the same check at the service layer (ADR-0002) — self-transfer and
  non-positive amount are pure request-shape validation, checked before any transaction opens, not
  business outcomes. `idempotency_key` is deliberately **not** duplicated onto this table;
  `idempotency_records` (below) is the single source of truth for it, linked 1:1 by `transfer_id`.
  A transfer referencing a wallet that doesn't exist cannot be inserted at all — the FK rejects it
  — which is exactly why wallet-existence is handled as a full-transaction-rollback case, not a
  persisted `FAILED` transfer (see ADR-0003).
- `ledger_entries(id UUID PK, transfer_id UUID FK NOT NULL -> transfers, wallet_id UUID FK ->
  wallets, type TEXT NOT NULL CHECK (type IN ('DEBIT','CREDIT')), amount BIGINT NOT NULL CHECK
  (amount > 0), created_at)` — `transfer_id NOT NULL` makes "a ledger row with no transfer"
  unrepresentable directly, closing that evaluation-guide question at the schema level rather than
  by convention. Indexed on `wallet_id` and `transfer_id` for balance-reconciliation and history
  lookups.
- `idempotency_records(idempotency_key TEXT PRIMARY KEY, request_fingerprint TEXT NOT NULL,
  transfer_id UUID NOT NULL UNIQUE REFERENCES transfers, created_at)` — see ADR-0002 for the
  strategy this backs; the `PRIMARY KEY`/`UNIQUE` pair is the actual race-free enforcement
  mechanism, not just an index for lookup speed.
- Additional indexes: `transfers(from_wallet_id)`, `transfers(to_wallet_id)` — support the
  optional transfer-history read API without a full scan.

## Consequences

**Good:** schema is versioned, reviewable code, applied identically everywhere by the same tool —
no drift between what tests run against and what a real deploy would apply. The constraints above
make several evaluation-guide questions true by construction: a ledger row cannot exist without a
transfer, a wallet cannot go negative, a transfer cannot self-reference, and idempotency-key
uniqueness is DB-enforced, not application-checked.

**Bad / risks:** Flyway is a JVM-based tool — mitigated by only ever invoking it through the
official Docker image, so no local JVM install is required in dev or CI. Forward-only migrations
mean a schema mistake is corrected with a new migration, never edited or rolled back in place —
accepted as the right tradeoff for a system meant to hold real financial data, not treated as a
limitation to work around.
