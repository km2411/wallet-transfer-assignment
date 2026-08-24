> Hand-maintained, not generated. Every top-level module/package under `src/wallet_transfer/`
> needs a matching section here — see `AGENTS.md` "Architecture documentation". Diagrams are
> plain ASCII/Unicode box-drawing text in an untagged fenced code block, not Mermaid — update the
> diagram in the same PR as the code that changes it, not as a separate rendering step.

## Executive Summary

<!-- One paragraph: what this service does, and the one or two design decisions that shape
     everything else (the thing a new reader most needs to know before reading any code). -->

## System Overview

```
<!-- Redraw whenever a top-level component is added, removed, or its role changes. Boxes for
     each component + external dependency (Postgres), arrows for the actual request/data flow. -->
```

## Components

Each top-level module directly under `src/wallet_transfer/` gets a `##` section: what it's for,
its public interface, what it depends on, and — if its internal flow isn't obvious from the
name — a small ASCII diagram.

<!-- ### handlers
Purpose: request validation, transport (HTTP) mapping, invoking service logic. No business logic.
Depends on: services

### services
Purpose: business logic, orchestration, idempotency behavior, transfer workflow.
Depends on: repositories, domain

### repositories
Purpose: persistence operations only — no workflow decisions.
Depends on: domain

### domain
Purpose: entities, state transitions, validation rules. Depends on nothing else in this project.
-->

## Known Limitations

<!-- Anything a reader would otherwise assume works but doesn't yet, or a shortcut taken
     deliberately. Keep this current — remove an item the moment it's resolved. -->

- **Observability is deliberately minimal.** No metrics/tracing infrastructure — out of scope for
  the time-box (`ASSIGNMENT.md` lists it under Optional Enhancements). Structured logging at the
  service-layer transaction boundary (attempt started, terminal outcome, retry count) is the only
  observability surface, sufficient to debug the scenarios this project actually covers.
- **Per-wallet throughput is serialized by design** (ADR-0003) — pessimistic row locking means a
  single wallet under extreme concurrent load (thousands of TPS) would need a different
  architecture (partitioned/sharded balance, or an async ledger-first design). Not solved here;
  named so it reads as a deliberate boundary, not an oversight.
- **No background worker or queue** (ADR-0008) — every transfer resolves within the request that
  created it; `PENDING` is never a durably observable status.
- **Single currency, implicit — multi-currency is explicitly out of scope.** `wallets`,
  `transfers`, and `ledger_entries` (ADR-0004) have no `currency` column: every wallet and every
  transfer is assumed to be the same, unspecified currency. No conversion, no per-currency
  balance, no check that a transfer's two wallets share a currency — there's nothing to compare.
  Not solved here; named explicitly so it reads as a deliberate boundary rather than something
  discovered by surprise later.
