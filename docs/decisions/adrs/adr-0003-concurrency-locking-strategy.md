---
kind: adr
status: proposed
owner: Kartik Mittal
last_reviewed: 2026-08-24
---

# ADR-0003 — Ordered pessimistic row locks with bounded retry for wallet debits

- **Status:** Proposed
- **Deciders:** Kartik Mittal (candidate)

## Context

Two transfers can attempt to debit the same wallet simultaneously, or two transfers can move
funds between the same pair of wallets in opposite directions at the same time. The system must
guarantee correct balances, no double-spend, and consistent ledger entries under both cases —
and per an explicit steer during design review, the strategy should hold up as a real production
default, not just pass a two-thread test in isolation: it must not let a hot wallet under load
queue requests indefinitely, and it must recover from transient contention rather than fail on
the first collision.

## Decision drivers

- No read-then-write race on wallet balance under concurrent same-wallet debits.
- No deadlock between two transfers on the same wallet pair moving funds in opposite directions.
- Bounded behavior under contention: a blocked request must fail fast and recoverably, not queue
  forever and exhaust the connection pool.
- Simple enough to implement and test correctly within the assignment's time-box, without
  pretending to solve horizontal scaling for a single hot wallet — that's a different problem.

## Considered options

1. **Optimistic locking** (a `version` column on `wallets`; `UPDATE ... WHERE version = X`, retry
   the whole operation on a zero-row update). Rejected as the default: re-runs the entire business
   logic attempt on every conflict rather than just waiting for a lock, and risks livelock under
   sustained contention on a genuinely hot wallet — no better predictability than pessimistic
   locking with bounded retry for the contended case that actually matters here, at higher
   implementation complexity (two rows — debit and credit — both need to agree).
2. **`SERIALIZABLE` isolation with application-level retry on serialization failure.** Rejected as
   the default: the right tool when the conflicting rows aren't known in advance (write skew
   across predicates) — that's not this problem, we know exactly which two wallet rows are
   involved. Using SSI here adds retry complexity and more false-positive aborts under contention
   without buying additional safety over locking the known rows directly.
3. **Pessimistic row-level locks (`SELECT ... FOR UPDATE`) on both wallets, acquired in a
   consistent order, with an explicit lock timeout and bounded retry on the caller side.**
   *Chosen.*

## Decision

Within a single database transaction per transfer attempt:

- Acquire `SELECT ... FOR UPDATE` locks on both the source and destination wallet rows, always in
  **ascending `wallet_id` order**, regardless of transfer direction. This is what actually
  prevents the deadlock case: two opposite-direction transfers on the same pair would otherwise
  each hold one wallet's lock while waiting on the other's.
- Set an explicit `lock_timeout` (e.g. 2s) for the transaction. A request blocked behind another
  fails fast with a distinct, retryable error instead of queuing indefinitely.
- The service layer wraps the transaction attempt in a **bounded retry** (e.g. up to 3 attempts,
  exponential backoff with jitter, via `asyncio.sleep()` — never a blocking sleep, per ADR-0007)
  for exactly two error classes: a `lock_timeout` and Postgres's
  own `deadlock_detected` (kept as defense-in-depth — the ordering rule above should make this
  unreachable, but a future code path that doesn't follow it would otherwise fail silently wrong
  instead of loudly retrying). **Insufficient funds is not retried** — it's a legitimate terminal
  result (`FAILED`), not a transient error.
- **Wallet existence is not a business-rule outcome (fixed on review — see ADR-0002's validation
  boundary).** `SELECT ... FOR UPDATE WHERE id = :wallet_id` returning zero rows means the wallet
  doesn't exist. This is discovered *inside* the transaction (locking is the only place that
  actually needs to look the row up), but it is handled by rolling back the **entire** transaction
  — idempotency-record insert included — and returning `404`, not by persisting a `FAILED`
  transfer. A transfer row referencing a nonexistent wallet cannot be inserted at all (FK
  constraint, ADR-0004), so there is no other consistent option. A retry with the same key after
  this simply re-runs the same check and fails the same way — safe without needing a stored
  record, same reasoning as ADR-0002's other pure-validation failures.
- **Retry exhaustion.** If all bounded-retry attempts still hit `lock_timeout`/`deadlock_detected`,
  the entire transaction (idempotency record included) has rolled back every time — nothing is
  persisted. The API returns a distinct, retryable `503` rather than a generic error. Because
  nothing was persisted, the client can safely retry later with the *same* idempotency key — there
  is no conflicting record to collide with.
- The locked critical section (lock → balance check → ledger insert → status update → commit)
  contains no external calls or unrelated queries, to bound how long the locks are actually held.
- Isolation level: Postgres's default `READ COMMITTED` is sufficient. The row locks are what
  provide the safety property, not the isolation level — and plain reads (e.g. a balance query)
  are never blocked by these write locks under MVCC, so this doesn't cost read availability.
- **Empirical validation, not just unit tests:** `scripts/simulate.py` seeds wallets and drives
  the real running HTTP API through concurrent load — same-wallet fan-in, opposite-direction
  pairs, duplicate-key replay — reporting a pass/fail balance reconciliation. This is what ADR-0005
  refers to as this ADR's demo tooling; it exists specifically because the automated test suite's
  concurrency tests (below) prove correctness at test-scale, and this is the way to see the same
  guarantees hold under a heavier, more realistic load, doubling as the interview demo.

### Required test scenarios (critical section) — repository/integration tier, real Postgres, real concurrent connections

- **CT1** — N concurrent transfers debiting the same source wallet where combined amount exceeds
  its balance: exactly the affordable ones succeed, the rest resolve `FAILED`, final balance is
  correct and never negative.
- **CT2** — two concurrent transfers on the same wallet pair in opposite directions (`W1→W2` and
  `W2→W1` simultaneously): both complete, no deadlock, proving the ascending-id lock ordering.
- **CT3** — forced lock contention (hold a lock open deliberately in one test transaction): the
  contending transaction hits `lock_timeout`, the bounded-retry wrapper retries, and it succeeds
  once the first releases.
- **CT4** — two concurrent requests with the *identical* idempotency key (double-fire): exactly
  one transfer is created, both callers receive the same terminal result, no duplicate ledger
  entries.
- **CT5** — two concurrent requests with the *same* idempotency key but a *different* payload: the
  loser gets `409`, no transfer is created for it.
- **CT6** — high-concurrency fan-in stress (e.g. 50-100 concurrent attempts against one wallet, via
  `scripts/simulate.py`): final balance reconciles exactly against the expected value.

## Consequences

**Good:** deadlock-safe by construction, not by convention. Predictable, bounded behavior under
contention instead of unbounded queuing. Deterministic enough to test directly: concurrent
same-wallet fan-in and concurrent opposite-direction pairs are both reproducible test scenarios
that either hold or don't, with no flaky timing dependence on retry semantics doing the real work.

**Bad / risks:** pessimistic locking serializes throughput **per wallet** — that's inherent to
this approach, not an oversight. A wallet under genuinely extreme load (thousands of TPS on one
wallet — a treasury/hub wallet, say) would eventually need a different architecture entirely
(partitioned/sharded balance, or an async ledger-first design with eventual consistency).
Recorded as a named ceiling in `ARCHITECTURE.md`'s Known Limitations once written, not solved
here — building for that scale now would be solving a problem this assignment doesn't have.
Bounded retry adds up to ~3x latency in the worst case under contention, which is the accepted
cost of failing safely and recoverably instead of failing on the first collision.
