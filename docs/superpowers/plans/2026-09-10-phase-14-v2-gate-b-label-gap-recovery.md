# Phase 14 V2 Gate B Label-Gap Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover safely from the 10 September 2026 Gate B `prepare` failure caused by a missing canonical non-holdout `official-outcome-v1` label, while preserving the already-frozen plan and keeping the final holdout unread until a separately approved resume.

**Architecture:** Keep the original feature-only `plan.json` immutable. Add a narrowly scoped canonical-label recovery path that derives its target set only from the frozen plan's non-holdout condition IDs, reuses official Gamma snapshots plus the existing `official-outcome-v1` label semantics, and never queries/fetches a final-holdout condition. Add a separate exact-main resume helper that consumes the existing partial evidence directory, refuses to create a new plan, requires selection/holdout/summary to be absent, and can continue `prepare -> evaluate-holdout -> summary` only after the recovery and a fresh explicit execution approval.

**Tech Stack:** Python 3.12, SQLAlchemy, existing BP Gamma client and historical/label repositories, Bash exact-main Cloud Shell helpers, pytest, GitHub Actions.

**Spec:** `docs/superpowers/plans/2026-09-09-phase-14-v2-gate-b-research.md`

## Global Constraints

- Preserve `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.
- Preserve frozen V2 Gate B geometry, freshness candidates `[1, 2, 5, 10]`, explicit `no_trade`, fee/slippage assumptions, min-edge grid, and minimum market counts.
- Do not create a replacement Gate B plan or choose another holdout from the failed result.
- The existing `/var/lib/bp/evidence/phase14-v2-gate-b-20260910T102812Z/plan.json` remains the only accepted plan for this recovery path.
- Before resume, final-holdout labels/outcomes must remain unread by Gate B recovery/audit code and no `selection.json`, `holdout.json`, or `summary.json` may exist.
- Any canonical-label repair is append-only production evidence mutation and therefore must remain behind a separate explicit production approval gate.
- `automatic_promotion=false`; no prospective V2 activation, Phase 15, geographic bypass, live trading, or nonzero money is authorized.

---

### Task 1: Make canonical label generation safely condition-scoped

**Files:**
- Modify: `tests/labels/test_label_service.py`
- Modify: `src/bp_engine/labels/service.py`

**Interfaces:**
- Extends `generate_labels(..., condition_ids: tuple[str, ...] | None = None)`.
- When `condition_ids` is provided, the SQL query itself must filter to those condition IDs before snapshot payloads are parsed.

- [ ] **Step 1: Write the failing test**

Add a test that stores one valid requested resolved snapshot and one unrequested malformed/conflicting snapshot. Call `generate_labels(..., condition_ids=(requested_id,))` and assert the requested label is created without inspecting/failing on the unrequested snapshot.

- [ ] **Step 2: Run the focused label test and verify RED**

Run: `pytest tests/labels/test_label_service.py -q`

Expected: FAIL because `generate_labels` does not yet accept `condition_ids`.

- [ ] **Step 3: Implement the minimal SQL filter**

Validate `condition_ids` when supplied, then add `WHERE polymarket_market_snapshots.condition_id IN (...)` before `.order_by(...)`. Keep default behavior byte-for-byte equivalent when the argument is omitted.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest tests/labels/test_label_service.py -q`

Expected: PASS.

### Task 2: Add frozen-plan non-holdout label audit and recovery service

**Files:**
- Create: `src/bp_engine/v2_research/label_recovery.py`
- Create: `tests/v2_research/test_gate_b_label_recovery.py`

**Interfaces:**
- Produces `audit_gate_b_non_holdout_labels(connection, *, plan) -> dict[str, object]`.
- Produces async `recover_gate_b_non_holdout_labels(engine, client, *, plan, observed_at) -> dict[str, object]`.
- Consumes the existing Gate B plan verification/non-holdout membership logic, `market_features`, `market_labels`, `HistoricalRepository`, `GammaClient`, `parse_gamma_market`, and condition-scoped `generate_labels`.

- [ ] **Step 1: Write failing audit tests**

Prove the audit:
- verifies the frozen plan;
- queries only non-holdout condition IDs;
- reports complete V2 feature identity and missing canonical labels;
- never includes a final-holdout condition even when its label is absent.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/v2_research/test_gate_b_label_recovery.py -q`

Expected: FAIL because the recovery module does not exist.

- [ ] **Step 3: Implement the minimal read-only audit**

Use the frozen plan's non-holdout membership. Require exactly the four V2 feature offsets and consistent static metadata for every requested condition. Query `official-outcome-v1` labels only for that non-holdout ID set. Return deterministic counts/details and `holdout_touched=false`.

- [ ] **Step 4: Add failing recovery tests**

Cover:
- an already-labeled non-holdout market is skipped without a Gamma request;
- a missing non-holdout label fetches official Gamma, validates identity, appends a historical snapshot, and creates an `official-outcome-v1` label;
- final-holdout conditions are never fetched;
- unresolved Gamma stays pending and does not create a label;
- identity mismatch fails closed;
- rerun is idempotent.

- [ ] **Step 5: Implement minimal canonical recovery**

Fetch only missing non-holdout conditions. Require exact condition/slug/horizon/window identity and a closed resolved market. Store the immutable Gamma snapshot, then call condition-scoped `generate_labels` for exactly that condition. Never read or mutate holdout evidence.

- [ ] **Step 6: Verify GREEN**

Run: `pytest tests/v2_research/test_gate_b_label_recovery.py tests/labels/test_label_service.py -q`

Expected: PASS.

### Task 3: Add operator CLI and exact-main recovery helper

**Files:**
- Create: `scripts/run_v2_gate_b_label_recovery.py`
- Create: `src/bp_engine/v2_research/label_recovery_cli.py`
- Create: `scripts/deploy/phase14_v2_gate_b_label_recovery_cloudshell.sh`
- Create: `tests/v2_research/test_gate_b_label_recovery_deployment.py`

**Interfaces:**
- CLI commands: `audit` (read-only) and `recover` (append-only canonical non-holdout snapshot/label repair).
- Cloud Shell helper inputs include exact helper head, accepted deployed head, accepted storage evidence, frozen partial evidence directory, and explicit recovery approval.

- [ ] **Step 1: Write failing deployment/CLI tests**

Require the helper to:
- bind clean local `main` and unchanged remote `main`;
- bind deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5` and storage evidence digest;
- require the partial evidence directory to be absolute and contain `plan.json` only among Gate B artifacts;
- reject any existing `selection.json`, `holdout.json`, or `summary.json`;
- run `audit` before any mutation;
- require an explicit `PHASE14_V2_GATE_B_LABEL_RECOVERY_APPROVED=true` before `recover`;
- preserve research/zero-money services and storage health before and after;
- never invoke Gate B `plan`, `prepare`, or `evaluate-holdout`.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/v2_research/test_gate_b_label_recovery_deployment.py -q`

Expected: FAIL because the helper/CLI do not exist.

- [ ] **Step 3: Implement CLI and helper**

The helper may copy an exact-main archive to `/tmp` and execute it from `/var/tmp`, but must not alter `/opt/bp`, systemd state, or live/money controls. `audit` is always allowed; `recover` remains fail-closed without explicit approval. Emit deterministic evidence including plan hash, missing IDs before/after, created/existing snapshot/label counts, and `HOLDOUT_TOUCHED=false`.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/v2_research/test_gate_b_label_recovery_deployment.py tests/v2_research/test_gate_b_label_recovery.py -q`

Expected: PASS.

### Task 4: Add frozen-plan Gate B resume helper

**Files:**
- Create: `scripts/deploy/phase14_v2_gate_b_resume_cloudshell.sh`
- Create: `tests/v2_research/test_gate_b_resume_deployment.py`

**Interfaces:**
- Consumes the existing partial evidence directory and its immutable `plan.json`.
- Produces only `selection.json`, `holdout.json`, and `summary.json` in that same directory.

- [ ] **Step 1: Write failing resume-helper tests**

Require the helper to:
- refuse if `plan.json` is absent;
- refuse if `selection.json`, `holdout.json`, or `summary.json` already exists;
- never invoke the `plan` command;
- verify the plan hash/frozen research config before prepare;
- require a successful non-holdout label audit with zero missing labels;
- require a distinct explicit resume approval before `prepare`/`evaluate-holdout`;
- run `prepare`, verify `holdout_labels_read=false`, then run exactly one `evaluate-holdout`;
- preserve all research/zero-money and deployed-head/storage-health checks;
- mark holdout touched only if `holdout.json` exists.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/v2_research/test_gate_b_resume_deployment.py -q`

Expected: FAIL because the resume helper does not exist.

- [ ] **Step 3: Implement the resume helper**

Reuse the existing Gate B verification blocks, but remove plan creation entirely. Bind the frozen plan file already present in the partial evidence directory. Use exact-main candidate code only for verification/prepare/evaluate logic; never select a new holdout.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/v2_research/test_gate_b_resume_deployment.py -q`

Expected: PASS.

### Task 5: Record the failed attempt and new recovery boundary

**Files:**
- Create: `docs/evidence/phase-14-v2-gate-b-pre-holdout-label-gap-20260910.json`
- Modify: `PROJECT_STATE.json`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Modify: `docs/DECISION-LOG.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Bump source-of-truth version to `0.14.125`.
- Record the 10 September attempt as a pre-holdout failure with `PLAN_PRESENT=true`, `SELECTION_PRESENT=false`, `HOLDOUT_PRESENT=false`, `SUMMARY_PRESENT=false`, and `HOLDOUT_TOUCHED=false`.

- [ ] **Step 1: Add sanitized evidence**

Record helper head `0941684676ea35949ac938a3bca33012b6ea0e19`, deployed head `e9c7afc1536880e4612cb6e3d1a7282fa37c69f5`, accepted storage evidence digest, candidate archive digest `a3bc68cc85d379998ef19ee851619cd3e6195c8ef49aa836dafc5aff9658c89c`, missing condition `0x15a96b013fa3009dc15f83df516116b583cce49325e313d578e51b8b5d9b70e8`, partial evidence directory, transcript basename/digest, and exit code 1. Do not store the user's home-directory path.

- [ ] **Step 2: Update state/docs**

Classify the root cause as a pipeline population mismatch: V2 forward features cover completed markets outcome-blind, while the always-on prospective outcome sync acquires canonical labels only for ended `live_predictions`. Mark the original one-shot execution as attempted/failed pre-holdout and consumed; require fresh explicit approval for append-only label repair and for final-holdout resume. Keep all live/money/promotion controls blocked.

- [ ] **Step 3: Run source-of-truth contract tests**

Run: `pytest tests/v2_research/test_gate_b_contract.py -q`

Expected: PASS after assertions are updated if necessary.

### Task 6: Full verification and merge packaging

- [ ] Run focused V2/label tests.
- [ ] Run Ruff on touched Python files.
- [ ] Run the full test suite and deployment validation through CI.
- [ ] Open a draft PR from `phase14-v2-gate-b-label-gap-recovery-20260910` to `main`.
- [ ] Require exact-head CI, Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak to pass.
- [ ] Merge only after the exact-head gates pass and `main` has not advanced unexpectedly.
- [ ] Require post-merge main CI before presenting any production recovery command.
