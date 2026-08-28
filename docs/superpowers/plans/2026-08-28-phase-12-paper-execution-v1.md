# Phase 12 Paper Execution V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, append-only paper execution worker that turns only immutable `trade=true` 5m/15m signals into conservative simulated fills and official-outcome settlements, then exposes evidence-backed paper P&L in Dashboard V1.

**Architecture:** Add a generic execution request/protocol boundary and a paper-only adapter. Reconstruct Polymarket depth from immutable raw recorder events at causal simulated timestamps, persist order/fill/terminal/settlement ledgers without mutation, derive the $100 paper account from cash flows rather than a mutable balance row, and extend the read-only dashboard. The production worker remains money-disabled and contains no live-order client.

**Tech Stack:** Python 3.12, dataclasses/Decimal, SQLAlchemy 2, PostgreSQL 16, pytest, Next.js 16.3.3/TypeScript 6, systemd, Bash deployment acceptance.

**Spec:** `docs/superpowers/specs/2026-08-28-phase-12-paper-execution-v1-design.md`

## Global Constraints

- `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain unchanged.
- Default paper scenario: `$100.00` starting cash, `$5.00` target notional, `250 ms` latency, `2000 ms` order TTL.
- Paper orders may be created only from immutable `live_predictions` with `trade=true`, `executable=true`, a valid selected side, and selected ask.
- Use only recorder evidence with `received_at <=` the simulated observation timestamp; never synthesize unavailable depth.
- V1 buys the selected outcome token and holds filled shares to official resolution; no speculative early-exit strategy.
- All money/share arithmetic in the execution layer uses `Decimal` and lossless PostgreSQL `NUMERIC(38,18)` storage.
- Paper ledgers are append-only/idempotent. A natural-key collision with different semantic content raises and never overwrites.
- No wallet, signer, allowance, credential, CLOB order placement, or real cancellation implementation may be added.

---

### Task 1: Execution domain contract and paper configuration

**Files:**
- Create: `src/bp_engine/execution/__init__.py`
- Create: `src/bp_engine/execution/models.py`
- Create: `src/bp_engine/execution/protocol.py`
- Create: `tests/execution/__init__.py`
- Create: `tests/execution/test_models.py`

**Interfaces:**
- Produces `PAPER_EXECUTION_VERSION = "paper-execution-v1"`.
- Produces immutable `PaperExecutionConfig`, `ExecutionOrderRequest`, `ExecutionOrderAck`, and `ExecutionCancelAck`.
- Produces `ExecutionGateway` protocol with `submit_order()` and `cancel_order()` methods.

- [ ] **Step 1: Write the failing validation/protocol tests**

```python
from decimal import Decimal

import pytest

from bp_engine.execution.models import PaperExecutionConfig


def test_default_paper_config_is_money_disabled_research_scenario() -> None:
    config = PaperExecutionConfig()
    assert config.starting_cash_usd == Decimal("100.00")
    assert config.target_notional_usd == Decimal("5.00")
    assert config.latency_ms == 250
    assert config.order_ttl_ms == 2000
    assert config.execution_version == "paper-execution-v1"


def test_paper_config_rejects_non_positive_sizing() -> None:
    with pytest.raises(ValueError):
        PaperExecutionConfig(target_notional_usd=Decimal("0"))
```

Add a test that a stub object implementing `submit_order(request)` and `cancel_order(order_id, observed_at)` satisfies the runtime-checkable `ExecutionGateway` protocol, while the package contains no `LiveExecutionGateway` symbol.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest tests/execution/test_models.py -q`
Expected: import/module failures because `bp_engine.execution` does not exist.

- [ ] **Step 3: Implement minimal immutable domain types**

Use frozen dataclasses and `Decimal` fields. `PaperExecutionConfig` validates positive paper-only values and exposes a canonical mapping used later for hashing. `ExecutionOrderRequest` must contain prediction/hash, condition/token/side, action `BUY`, requested shares/notional, submitted/arrival/expiry timestamps, limit price, and execution config version/hash.

```python
@runtime_checkable
class ExecutionGateway(Protocol):
    def submit_order(self, request: ExecutionOrderRequest) -> ExecutionOrderAck: ...
    def cancel_order(self, order_id: str, observed_at: datetime) -> ExecutionCancelAck: ...
```

Do not add a live adapter.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest tests/execution/test_models.py -q && ruff check src/bp_engine/execution tests/execution`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: define paper execution contract`

---

### Task 2: Append-only paper execution schema and repository

**Files:**
- Modify: `src/bp_engine/storage/schema.py`
- Create: `migrations/0011_paper_execution.sql`
- Create: `src/bp_engine/execution/repository.py`
- Create: `tests/execution/test_repository_postgres.py`

**Interfaces:**
- Produces SQLAlchemy tables `paper_orders`, `paper_fills`, `paper_order_terminal_events`, `paper_settlements`.
- Produces `PaperExecutionRepository.insert_order`, `insert_fill`, `insert_terminal_event`, `insert_settlement`, and read methods used by the service/dashboard.

- [ ] **Step 1: Write PostgreSQL RED tests for append-only/idempotent semantics**

Create schema on the CI PostgreSQL database, insert a source `live_prediction`, then assert:

```python
first = repository.insert_order(order)
second = repository.insert_order(order)
assert first.created is True
assert second.created is False
```

Construct a second order with the same `(prediction_id, execution_version)` but a different semantic hash and assert `PaperLedgerConflictError`. Repeat the same pattern for fill natural key `(paper_order_id, fill_key)`, terminal natural key `paper_order_id`, and settlement `(paper_order_id, label_version)`.

- [ ] **Step 2: Run focused PostgreSQL tests and verify RED**

Run: `BP_TEST_DATABASE_URL=... pytest tests/execution/test_repository_postgres.py -q`
Expected: missing table/repository failures.

- [ ] **Step 3: Add schema/migration and repository**

Use `NUMERIC(38,18)` for prices, shares, costs, fees, payout, and P&L. Every ledger stores `semantic_sha256` and `created_at`; no `UPDATE` method exists. Add indexes for order prediction/submitted time, fill order/time, terminal time, and settlement time. Foreign-key constraints are intentionally not introduced if the existing schema convention avoids them; application-level reconciliation must still require exact source rows.

Repository inserts use PostgreSQL `ON CONFLICT DO NOTHING`, then fetch the existing row and compare all semantic fields. Exact match returns `created=False`; mismatch raises `PaperLedgerConflictError`.

- [ ] **Step 4: Run focused tests plus migration/schema checks**

Run: `pytest tests/execution/test_repository_postgres.py -q && ruff check src/bp_engine/execution src/bp_engine/storage/schema.py tests/execution`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: add immutable paper execution ledger`

---

### Task 3: Causal Polymarket full-book replay

**Files:**
- Create: `src/bp_engine/execution/book.py`
- Create: `tests/execution/test_book.py`
- Create/modify: `tests/execution/test_book_postgres.py`

**Interfaces:**
- Produces `BookLevel(price: Decimal, size: Decimal)` and `ReplayedBook`.
- Produces `PolymarketBookReplayReader.book_at(connection, condition_id, asset_id, observed_at) -> ReplayedBook | None`.
- `ReplayedBook` includes ordered bids/asks, anchor event id/dedupe key, applied event ids/dedupe keys, and `replay_cutoff_at`.

- [ ] **Step 1: Write RED unit tests for raw payload reduction**

Use a full book anchor such as:

```python
book = {"event_type": "book", "market": "condition", "asset_id": "up", "bids": [{"price": "0.54", "size": "10"}], "asks": [{"price": "0.56", "size": "2"}, {"price": "0.57", "size": "4"}]}
change = {"event_type": "price_change", "market": "condition", "price_changes": [{"asset_id": "up", "side": "SELL", "price": "0.56", "size": "1.5"}]}
```

Assert asks become `[(0.56, 1.5), (0.57, 4)]`, zero size removes a level, changes for the other token are ignored, and malformed/negative price or size raises `BookReplayError` instead of guessing.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest tests/execution/test_book.py -q`
Expected: module/type failures.

- [ ] **Step 3: Implement deterministic reducer and PostgreSQL reader**

The reader first selects the latest selected-token `book` row with `received_at <= observed_at`, then selects condition-level `price_change` rows after the anchor through `observed_at` ordered by `(received_at, id)`. Apply only changes whose `asset_id` equals the requested token. Validate `0 <= bid < ask <= 1` only when both sides are present; never manufacture a missing side.

- [ ] **Step 4: Add PostgreSQL causality tests**

Insert an anchor, a change before cutoff, and a better ask after cutoff. Assert the after-cutoff event is absent from the replay and provenance. Assert no anchor returns `None`.

Run: `pytest tests/execution/test_book.py tests/execution/test_book_postgres.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: replay causal polymarket books`

---

### Task 4: Paper order factory, depth walk, partial fills, fees, and cash

**Files:**
- Create: `src/bp_engine/execution/paper.py`
- Create: `tests/execution/test_paper.py`

**Interfaces:**
- Produces `build_paper_order(prediction, config, available_cash) -> PaperOrderDraft | PaperTerminalDraft`.
- Produces `simulate_buy(order, books) -> PaperSimulationResult` where `books` is a chronological sequence of distinct/replenished `ReplayedBook` observations through expiry.
- Produces deterministic fill/terminal drafts later persisted by repository.

- [ ] **Step 1: Write RED tests for eligibility/sizing**

Assert `trade=false`, `executable=false`, invalid side, or missing selected ask fail closed without an order. For a `$100` account, `$5` target, ask `0.50`, slippage buffer `0.01`, and fee rate `0.07`, assert requested shares are positive, rounded down, and never exceed both target sizing and worst-case cash capacity.

- [ ] **Step 2: Write RED tests for fills and partial fills**

Create a request for 10 shares with a `0.58` limit and books containing asks `0.56 x 2`, `0.57 x 3`, `0.59 x 10`. Assert exactly 5 shares fill, none at `0.59`, each price level becomes a separate fill, realized slippage is measured from the source signal ask, and the remainder expires if no new eligible liquidity appears.

Add a later replay observation with an explicit increase/new eligible ask level and assert only newly available/replenished depth can fill the outstanding remainder; unchanged displayed depth is never reused for a second fill.

- [ ] **Step 3: Verify RED**

Run: `pytest tests/execution/test_paper.py -q`
Expected: missing implementation failures.

- [ ] **Step 4: Implement deterministic paper math**

Use `Decimal` exclusively. Per-fill fee is:

```python
fee_per_share = fee_rate * fill_price * (Decimal("1") - fill_price)
fee = shares * fee_per_share
```

Total fill cost is `shares * fill_price + fee`. Fill timestamps must be within `[arrival_at, expires_at]`. Terminal reason is derived from fill state (`FILLED`, `EXPIRED`, `MARKET_ENDED_UNFILLED`, or `INSUFFICIENT_PAPER_CASH`). No random latency/slippage is used in V1.

- [ ] **Step 5: Run focused tests and lint**

Run: `pytest tests/execution/test_paper.py -q && ruff check src/bp_engine/execution tests/execution`
Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: simulate conservative paper fills`

---

### Task 5: Signal-to-settlement paper execution service

**Files:**
- Create: `src/bp_engine/execution/service.py`
- Create: `src/bp_engine/execution/cli.py`
- Create: `src/bp_engine/execution/__main__.py`
- Create: `scripts/run_paper_execution.py`
- Create: `tests/execution/test_service.py`
- Create: `tests/execution/test_service_postgres.py`

**Interfaces:**
- Produces `PaperExecutionService.run_once(now) -> PaperRunReport`.
- Produces CLI `python -m bp_engine.execution --once` and continuous polling mode.

- [ ] **Step 1: Write RED service tests**

Seed two immutable predictions: one `trade=false`, one `trade=true`. Assert only the eligible prediction can create an order. Seed raw book evidence and assert the service persists deterministic fills/terminal event. Run `run_once` twice and assert counts/semantic hashes remain unchanged.

- [ ] **Step 2: Add settlement RED tests**

Before an official `live_prediction_evaluation` exists, assert no `paper_settlement`. Add an append-only evaluation after market end and assert payout is `$1 * filled_shares` only when the selected side matches the official outcome, otherwise zero. Assert `realized_pnl = payout - total_fill_cost` and a rerun is idempotent.

- [ ] **Step 3: Verify RED**

Run: `pytest tests/execution/test_service.py tests/execution/test_service_postgres.py -q`
Expected: missing service failures.

- [ ] **Step 4: Implement orchestration and derived cash**

Derive current paper cash as:

```text
starting_cash - sum(all fill total_cost) + sum(all settlement payout)
```

Process predictions in stable `(recorded_at, id)` order. The service must never mutate `live_predictions`, raw events, labels, or prior paper rows. Store config version/hash on every order. Continuous mode sleeps between bounded `run_once` calls and handles SIGTERM cleanly.

- [ ] **Step 5: Run service/integration/full backend tests**

Run: `pytest tests/execution -q && pytest -q && ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

Commit message: `feat: run paper execution lifecycle`

---

### Task 6: Dashboard paper account, reconciliation, and read-only UI

**Files:**
- Modify: `src/bp_engine/dashboard/repository.py`
- Modify: `src/bp_engine/dashboard/service.py`
- Modify: `apps/dashboard/lib/snapshot.ts`
- Modify: `apps/dashboard/lib/presenter.ts`
- Modify: `apps/dashboard/app/dashboard-client.tsx`
- Modify: `tests/dashboard/test_service.py`
- Modify: `tests/dashboard/test_repository_postgres.py`
- Modify: `apps/dashboard/tests/presenter.test.ts`

**Interfaces:**
- Dashboard `mode.paper_execution_available` becomes `true`; `mode.execution_available` remains `false`.
- `paper_pnl` becomes an evidence object; add bounded `paper_orders`, `paper_fills`, and `paper_settlements` lists.

- [ ] **Step 1: Write backend RED tests**

Seed paper orders/fills/settlements and assert starting/current cash, realized P&L, return, counts, total fees/slippage, open capital, and reconciliation status. With no paper rows, assert a real `$100` starting-cash scenario with zero activity is distinguishable from missing schema/data; never reuse the old `UNAVAILABLE_UNTIL_PHASE_12` sentinel after Phase 12 support is installed.

- [ ] **Step 2: Write frontend RED tests**

Assert presenter output displays paper cash/P&L only from supplied snapshot fields, labels open/no-fill/settled counts, and never renders an order/cancel control.

- [ ] **Step 3: Implement read model and UI**

Keep queries read-only and bounded for history tables. Compute max realized-equity drawdown chronologically from `starting_cash + cumulative realized_pnl` over settlements. Unrealized value is `null` unless a fresh selected-token bid can be supported by current evidence; do not block Phase 12 acceptance on unrealized marking.

- [ ] **Step 4: Run backend/frontend verification**

Run backend: `pytest tests/dashboard tests/execution -q && ruff check .`
Run frontend: `npm --prefix apps/dashboard test && npm --prefix apps/dashboard run typecheck && npm --prefix apps/dashboard run build`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: expose paper execution dashboard`

---

### Task 7: Money-disabled worker deployment and exact-head host acceptance

**Files:**
- Create: `deploy/systemd/bp-paper-execution.service`
- Create: `scripts/deploy/phase12_host_acceptance.sh`
- Create: `scripts/deploy/phase12_cloudshell_accept.sh`
- Create: `scripts/deploy/phase12_install.sh`
- Create: `docs/PHASE-12-DEPLOYMENT.md`
- Create: `tests/execution/test_phase12_deployment_assets.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Permanent service runs as `bp`, starts only in research/live-disabled/zero-real-limit environment, writes only paper ledgers, and has no wallet/order credentials.
- Acceptance emits `PHASE12_HOST_ACCEPTANCE=PASS`; install emits `PHASE12_INSTALL=PASS` only after exact-head safety/reconciliation checks.

- [ ] **Step 1: Write RED deployment-contract tests**

Assert systemd unit has `User=bp`, `Group=bp`, `NoNewPrivileges=true`, protected filesystem/home, no public listener, and command runs `python -m bp_engine.execution`. Assert acceptance/install scripts contain exact-head verification, research/live-disabled/zero-real-limit gates, recorder/Postgres/dashboard continuity checks, paper ledger reconciliation, idempotent rerun, and no wallet/order-side-effect path.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/execution/test_phase12_deployment_assets.py -q`
Expected: missing asset failures.

- [ ] **Step 3: Implement deployment assets**

Candidate acceptance must use isolated source/runtime, run migrations/schema setup safely, process prospective eligible signals, prove fills are causal or explicitly no-fill, require zero reconciliation violations, rerun idempotently, keep all Phase 11 services healthy, and record sanitized evidence under `/var/lib/bp/evidence/phase12-paper-execution/<UTC_TIMESTAMP>/`.

The permanent installer must rollback only the paper worker/dashboard changes it owns if activation fails and must never stop/restart the recorder to make acceptance pass.

- [ ] **Step 4: Verify full CI candidate**

Run through GitHub Actions on the exact candidate: Python lint/full pytest/deployment syntax/health plus dashboard test/typecheck/build. Do not proceed to host commands unless both lanes are green.

- [ ] **Step 5: Commit**

Commit message: `deploy: add phase12 paper execution gate`

---

### Task 8: Production evidence, closeout, and Phase 13 handoff

**Files:**
- Create after host PASS: `docs/evidence/phase-12-closeout-20260828.json` (date may roll forward if host acceptance does)
- Modify after host PASS: `PROJECT_STATE.json`
- Modify after host PASS: `docs/CHANGELOG.md`
- Modify after host PASS: `START-HERE.md`
- Modify after host PASS: `README.md`

**Interfaces:**
- Preserve the exact host-accepted operational SHA separately from the docs-only closeout SHA.

- [ ] **Step 1: Run isolated production-host acceptance on exact green candidate**

Require prospective 5m/15m paper evidence where eligible signals occur; if a bounded window has no `trade=true` signal, record that honestly and extend the acceptance window rather than fabricating an order.

- [ ] **Step 2: Permanently install exact accepted SHA**

Require `PHASE12_INSTALL=PASS`, worker/dashboard/recorder/Postgres active, no real-order side effects, and real trading settings still fail-closed.

- [ ] **Step 3: Record sanitized closeout evidence**

Include paper config, order/fill/settlement/reconciliation counts, realized P&L only if evidence exists, no-fill reasons, exact source hashes, service status, and explicit statement that paper P&L is not a live-profitability claim.

- [ ] **Step 4: Update canonical state to Phase 13 only after evidence**

Set `source_of_truth_version` to `0.12.0`, add `phase_12_checkpoint`, mark Paper trading complete, and set next phase to Improvement Loop. Keep Live trading incomplete and disabled.

- [ ] **Step 5: Run fresh closeout CI and merge**

Final docs-only tree must pass the normal CI lanes before merge to `main`.
