# Phase 14 V2 Forward Coverage Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a restart-safe, research-only systemd timer that continuously materializes immutable timestamp-coherent 5m `core-v2-last-trade` rows for fully completed post-Gate-A markets and emits outcome-blind coverage diagnostics.

**Architecture:** Reuse the existing immutable V2 generator and coverage reporter. Add one focused orchestration module that discovers incomplete completed 5m markets from database state, one thin CLI that enforces the research/zero-money boundary and runs one cycle, hardened systemd oneshot/timer units, and an exact-head rollback-capable Cloud Shell rollout helper. Database natural keys are the checkpoint; no cursor file, label join, model policy, or trading path is introduced.

**Tech Stack:** Python 3.12, SQLAlchemy, PostgreSQL/SQLite tests, pytest, systemd, Bash, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md`

**Implementation status:** Tasks 1–5 complete and pre-packaging exact-head CI #1978 (`33639062997`) GREEN with 860 tests; Task 6 review packaging in progress. Production collector rollout remains separately gated and has not been performed.

## Global Constraints

- Canonical forward epoch is exactly `2026-09-02T12:18:02Z`.
- Operational post-end grace is exactly 15 seconds; it is not an economic freshness threshold and never changes feature timestamps.
- Only 5m markets (`horizon_seconds == 300`) are eligible.
- Only offsets 60, 120, 180, and 240 seconds may be materialized.
- Existing `core-v2-last-trade` rows are immutable and must be preserved.
- `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` are mandatory for every production cycle.
- Target discovery and coverage collection must not read labels, outcomes, prediction evaluations, calibration, P&L, paper settlements, or live-readiness evidence.
- Existing selected-book freshness remains exactly 10 seconds; no V2 last-trade freshness policy is selected.
- `policy_selected=false` and `automatic_promotion=false` remain hard invariants.
- No V2 live prediction, V2 paper execution, wallet/signing path, risk-limit change, geographic bypass, Phase 15 code, or real-money behavior is allowed.
- Production rollout is a separately guarded operational step after code review/merge.

---

## File Structure

### New files

- `src/bp_engine/features/v2_forward.py` — pure pending-target discovery and one-cycle orchestration.
- `src/bp_engine/features/v2_forward_cli.py` — settings/safety/transaction boundary and deterministic JSON CLI.
- `scripts/run_v2_forward_coverage.py` — thin executable wrapper.
- `deploy/bp-v2-forward-coverage.service` — hardened research-only oneshot service.
- `deploy/bp-v2-forward-coverage.timer` — one-minute persistent systemd timer.
- `scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh` — exact-head install/acceptance/rollback helper.
- `tests/features/test_v2_forward.py` — eligibility, outcome-blindness, partial/idempotent generation, causal-cutoff tests.
- `tests/features/test_v2_forward_cli.py` — CLI safety and deterministic output tests.
- `tests/deploy/test_phase14_v2_forward_coverage_deployment.py` — systemd/CI/rollout contract tests.
- `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json` — sanitized canonical copy of the successful Gate A host evidence.

### Modified files

- `.github/workflows/ci.yml` — syntax/compile validation for the new rollout helper and Python wrapper.
- `PROJECT_STATE.json` — record Gate A rollout PASS and forward coverage collector state.
- `docs/MASTER-SOURCE-OF-TRUTH.md` — record production Gate A acceptance and the outcome-blind forward epoch.
- `docs/BUILD-ORDER.md` — replace stale “Gate A not deployed” instructions with collector implementation/rollout next action.
- `docs/CHANGELOG.md` — record Gate A rollout acceptance and collector implementation checkpoint.
- `docs/DECISION-LOG.md` — append an operational continuation under D-033 only if needed to clarify the continuous outcome-blind epoch; do not create a new economic/policy decision.
- `docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md` — update status as implementation progresses.

---

### Task 1: Freeze Gate A rollout evidence and canonical project state

**Files:**
- Create: `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`
- Modify: `PROJECT_STATE.json`
- Modify: `docs/MASTER-SOURCE-OF-TRUTH.md`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md`

**Interfaces:**
- Consumes: user-supplied production evidence from `/var/lib/bp/evidence/phase14-v2-gate-a-rollout-20260902T122655Z.txt`.
- Produces: canonical machine-readable and human-readable checkpoint that later tasks treat as the forward-epoch authority.

- [ ] **Step 1: Write the sanitized evidence JSON**

Use this exact top-level structure and preserve the reported values rather than recomputing them:

```json
{
  "phase": 14,
  "evidence_type": "v2_gate_a_rollout_host_acceptance",
  "date": "2026-09-02",
  "verdict": "PASS",
  "old_head": "be1f82f65d15b2e172495e6ae934ec9a78648c32",
  "new_head": "d077e45f24704e6038c947169c84527e954de975",
  "rollout_started": "2026-09-02T12:18:02Z",
  "forward_epoch": "2026-09-02T12:18:02Z",
  "host_evidence_file": "/var/lib/bp/evidence/phase14-v2-gate-a-rollout-20260902T122655Z.txt",
  "safety": {
    "mode": "research",
    "live_trading_enabled": false,
    "max_trade_size_usd": 0,
    "max_daily_loss_usd": 0
  },
  "feature_validation": {
    "feature_version": "core-v2-last-trade",
    "forward_row_count": 4,
    "offsets": [60, 120, 180, 240],
    "future_source_cutoff": 0
  },
  "coverage": {
    "market_count": 1,
    "row_count": 4,
    "future_cutoff_violation_count": 0,
    "invalid_nonfinite_value_count": 0,
    "policy_selected": false,
    "automatic_promotion": false,
    "coverage_input_sha256": "44592883ca47d18337b4c4385f3e34badc00bc30e5a124a02c0c4c99cccf6891"
  },
  "observed_age_summary_seconds": {
    "selected_book_min": 0.420679,
    "selected_book_max": 1.109547,
    "up_last_trade_source_min": 2.471,
    "up_last_trade_source_max": 17.787,
    "down_last_trade_source_min": 4.247,
    "down_last_trade_source_max": 19.852
  },
  "soak_passed": true,
  "disk_before_status": "ok",
  "disk_after_status": "ok",
  "interpretation": "Operational Gate A acceptance only; one market/four rows is insufficient to choose V2 timing, freshness, calibration, edge, model, profitability, or promotion policy."
}
```

- [ ] **Step 2: Validate the evidence JSON parses**

Run:

```bash
python -m json.tool docs/evidence/phase-14-v2-gate-a-rollout-20260902.json >/dev/null
```

Expected: exit 0.

- [ ] **Step 3: Update canonical state**

Record all of the following without changing the Phase 14 live gate:

```text
Gate A deployed/accepted: PASS
production head: d077e45f24704e6038c947169c84527e954de975
forward epoch: 2026-09-02T12:18:02Z
feature version: core-v2-last-trade
first forward rows: 4 at 60/120/180/240
future cutoff violations: 0
policy_selected: false
automatic_promotion: false
live trading: false
money limits: zero
Phase 15: blocked
```

Remove stale wording that says Gate A is “not deployed” or “under review”. State explicitly that the next action is continuous outcome-blind forward coverage collection, not Gate B policy selection.

- [ ] **Step 4: Validate JSON and stale-state removal**

Run:

```bash
python -m json.tool PROJECT_STATE.json >/dev/null
! grep -n "Gate A has \*\*not\*\* been deployed" docs/BUILD-ORDER.md
! grep -n "Gate A implementation under review; code/test complete, not deployed" docs/superpowers/specs/2026-09-02-phase-14-timestamp-coherent-market-price-v2-design.md
```

Expected: all commands exit 0 after the old design status is updated where required.

- [ ] **Step 5: Commit**

```bash
git add PROJECT_STATE.json docs/MASTER-SOURCE-OF-TRUTH.md docs/BUILD-ORDER.md docs/CHANGELOG.md docs/evidence/phase-14-v2-gate-a-rollout-20260902.json docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md docs/superpowers/specs/2026-09-02-phase-14-timestamp-coherent-market-price-v2-design.md
git commit -m "docs: record V2 Gate A production acceptance"
```

---

### Task 2: Add pending-target discovery and one-cycle V2 orchestration

**Files:**
- Create: `src/bp_engine/features/v2_forward.py`
- Create: `tests/features/test_v2_forward.py`

**Interfaces:**
- Consumes: `V2FeatureTarget`, `V2_FEATURE_VERSION`, `generate_v2_features(...)`, `build_v2_coverage_report(...)`, `polymarket_markets`, and `market_features`.
- Produces:

```python
V2_FORWARD_EPOCH = datetime(2026, 9, 2, 12, 18, 2, tzinfo=UTC)
V2_FORWARD_END_GRACE_SECONDS = 15

@dataclass(frozen=True)
class V2ForwardCycleStats:
    cycle_at: datetime
    eligible_targets: int
    inserted: int
    existing: int
    planned_rows: int
    coverage_row_count: int
    coverage_market_count: int
    future_cutoff_violation_count: int
    policy_selected: bool
    automatic_promotion: bool


def discover_pending_v2_targets(
    connection: Connection,
    *,
    cycle_at: datetime,
    epoch: datetime = V2_FORWARD_EPOCH,
    end_grace_seconds: int = V2_FORWARD_END_GRACE_SECONDS,
) -> tuple[V2FeatureTarget, ...]: ...


def run_v2_forward_cycle(
    connection: Connection,
    *,
    cycle_at: datetime,
    epoch: datetime = V2_FORWARD_EPOCH,
    end_grace_seconds: int = V2_FORWARD_END_GRACE_SECONDS,
) -> V2ForwardCycleStats: ...
```

- [ ] **Step 1: Write RED eligibility tests**

Add fixtures containing:

```python
EPOCH = datetime(2026, 9, 2, 12, 18, 2, tzinfo=UTC)
CYCLE_AT = datetime(2026, 9, 2, 12, 30, 30, tzinfo=UTC)
```

Tests must assert:

```python
assert pre_epoch_target not in pending
assert active_target not in pending
assert fifteen_minute_target not in pending
assert completed_post_epoch_5m_target in pending
```

The active target must have `end_at + timedelta(seconds=15) > CYCLE_AT`; the eligible target must have `end_at + timedelta(seconds=15) <= CYCLE_AT`.

- [ ] **Step 2: Run RED eligibility tests**

Run:

```bash
pytest tests/features/test_v2_forward.py -v
```

Expected: FAIL because `bp_engine.features.v2_forward` does not exist.

- [ ] **Step 3: Implement minimal target discovery**

Implement a SELECT containing only these market columns:

```python
polymarket_markets.c.condition_id,
polymarket_markets.c.slug,
polymarket_markets.c.horizon_seconds,
polymarket_markets.c.start_at,
polymarket_markets.c.end_at,
polymarket_markets.c.up_token_id,
polymarket_markets.c.down_token_id,
```

Filter with:

```python
polymarket_markets.c.horizon_seconds == 300
polymarket_markets.c.start_at >= epoch
polymarket_markets.c.end_at <= cycle_at - timedelta(seconds=end_grace_seconds)
```

Determine incompleteness from `market_features` using only `condition_id`, `feature_at`, and `feature_version == V2_FEATURE_VERSION`. A target is pending if its stored V2 feature offsets are not exactly the complete set `{60, 120, 180, 240}`. Reject unexpected V2 offsets with `RuntimeError` rather than silently treating them as complete.

- [ ] **Step 4: Add RED completeness/idempotency tests**

Tests must cover:

```python
assert discover_pending_v2_targets(...complete_four_rows...) == ()
assert discover_pending_v2_targets(...partial_two_rows...) == (target,)
```

Also insert a synthetic `feature_offset_seconds=30` V2 row and assert discovery raises `RuntimeError("unexpected V2 forward feature offset")`.

- [ ] **Step 5: Run tests and make discovery GREEN**

Run:

```bash
pytest tests/features/test_v2_forward.py -v
```

Expected: PASS for eligibility/completeness tests.

- [ ] **Step 6: Write RED one-cycle tests**

Create source-state fixtures for one completed target with two already-existing V2 offsets. Assert:

```python
stats = run_v2_forward_cycle(connection, cycle_at=CYCLE_AT)
assert stats.eligible_targets == 1
assert stats.planned_rows == 4
assert stats.existing == 2
assert stats.inserted == 2
assert stats.coverage_row_count >= 4
assert stats.future_cutoff_violation_count == 0
assert stats.policy_selected is False
assert stats.automatic_promotion is False
```

Run the cycle a second time and assert `eligible_targets == 0` and no new row is inserted.

- [ ] **Step 7: Implement one-cycle orchestration**

Implementation must call:

```python
targets = discover_pending_v2_targets(...)
generation = generate_v2_features(
    connection,
    targets,
    generated_at=cycle_at,
    preserve_existing=True,
)
coverage = build_v2_coverage_report(connection)
```

Before returning, fail closed unless:

```python
coverage.future_cutoff_violation_count == 0
coverage.policy_selected is False
coverage.automatic_promotion is False
```

Do not import label, modeling, calibration, live prediction, execution, prospective outcome, or live-readiness modules.

- [ ] **Step 8: Add outcome-blindness source test**

In `tests/features/test_v2_forward.py`, inspect the module source and assert it contains none of:

```python
("official_outcome", "labels", "live_prediction_evaluations", "paper_settlements", "pnl", "calibration")
```

Also inspect the selected column names from the discovery statement or monkeypatch execution to prove only static identity/window/token fields plus V2 feature-key state are read.

- [ ] **Step 9: Run focused GREEN suite**

Run:

```bash
pytest tests/features/test_v2_forward.py tests/features/test_v2_future_data_leakage.py tests/features/test_v2_coverage.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/bp_engine/features/v2_forward.py tests/features/test_v2_forward.py
git commit -m "feat: collect pending forward V2 feature evidence"
```

---

### Task 3: Add fail-closed CLI and deterministic cycle output

**Files:**
- Create: `src/bp_engine/features/v2_forward_cli.py`
- Create: `scripts/run_v2_forward_coverage.py`
- Create: `tests/features/test_v2_forward_cli.py`

**Interfaces:**
- Consumes: `run_v2_forward_cycle(...)` and existing `Settings` fields.
- Produces: `main(argv: list[str] | None = None) -> int` with a single `once` command surface and deterministic JSON output.

- [ ] **Step 1: Write RED parser/safety tests**

Require the parser to expose only:

```text
once
--env-file
--database-url
--cycle-at  # test/acceptance override only; defaults to now UTC
```

Safety tests must construct settings variants and assert each non-research boundary fails before opening a write transaction:

```python
MODE != "research"
LIVE_TRADING_ENABLED is not False
MAX_TRADE_SIZE_USD != 0
MAX_DAILY_LOSS_USD != 0
```

Expected error text:

```text
V2 forward coverage requires RESEARCH/live-disabled/zero-money safety
```

- [ ] **Step 2: Run RED CLI tests**

Run:

```bash
pytest tests/features/test_v2_forward_cli.py -v
```

Expected: FAIL because CLI module/script do not exist.

- [ ] **Step 3: Implement the CLI**

Use a helper:

```python
def require_research_zero_money(settings: Settings) -> None:
    if (
        settings.mode != "research"
        or settings.live_trading_enabled is not False
        or settings.max_trade_size_usd != 0
        or settings.max_daily_loss_usd != 0
    ):
        raise ValueError(
            "V2 forward coverage requires RESEARCH/live-disabled/zero-money safety"
        )
```

Run safety validation before `engine.begin()`. Do not call `metadata.create_all()` in the recurring collector; production schema already exists and this subsystem adds no migration.

Serialize `V2ForwardCycleStats` with sorted JSON keys and ISO-8601 UTC `cycle_at`.

- [ ] **Step 4: Implement thin wrapper**

`scripts/run_v2_forward_coverage.py` must contain only:

```python
from bp_engine.features.v2_forward_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Test deterministic output and idempotency**

Assert a fixed `--cycle-at 2026-09-02T12:30:30Z` produces stable key/value output and a second `once` call inserts no duplicate V2 rows.

- [ ] **Step 6: Run focused GREEN suite**

Run:

```bash
pytest tests/features/test_v2_forward.py tests/features/test_v2_forward_cli.py -v
python -m py_compile src/bp_engine/features/v2_forward.py src/bp_engine/features/v2_forward_cli.py scripts/run_v2_forward_coverage.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/bp_engine/features/v2_forward_cli.py scripts/run_v2_forward_coverage.py tests/features/test_v2_forward_cli.py
git commit -m "feat: add safe V2 forward coverage cycle CLI"
```

---

### Task 4: Add hardened systemd oneshot/timer deployment surface

**Files:**
- Create: `deploy/bp-v2-forward-coverage.service`
- Create: `deploy/bp-v2-forward-coverage.timer`
- Create: `tests/deploy/test_phase14_v2_forward_coverage_deployment.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `/opt/bp/scripts/run_v2_forward_coverage.py`, `/etc/bp/bp.env`, `/etc/bp/bp-prospective-runtime-safety.env`.
- Produces: one unprivileged oneshot service and one persistent one-minute timer.

- [ ] **Step 1: Write RED systemd contract tests**

Assert the service contains exactly these safety/runtime lines:

```text
Type=oneshot
User=bp
Group=bp
WorkingDirectory=/opt/bp
EnvironmentFile=/etc/bp/bp.env
EnvironmentFile=/etc/bp/bp-prospective-runtime-safety.env
Environment=MODE=research
Environment=LIVE_TRADING_ENABLED=false
Environment=MAX_TRADE_SIZE_USD=0
Environment=MAX_DAILY_LOSS_USD=0
ExecStart=/opt/bp/.venv/bin/python /opt/bp/scripts/run_v2_forward_coverage.py once --env-file /etc/bp/bp.env
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=full
RestrictAddressFamilies=AF_UNIX
```

The collector itself needs only PostgreSQL over localhost/unix transport and no external HTTP/WebSocket access.

Assert the timer contains:

```text
OnBootSec=1min
OnUnitActiveSec=1min
Persistent=true
Unit=bp-v2-forward-coverage.service
WantedBy=timers.target
```

- [ ] **Step 2: Run RED deployment tests**

Run:

```bash
pytest tests/deploy/test_phase14_v2_forward_coverage_deployment.py -v
```

Expected: FAIL because units do not exist.

- [ ] **Step 3: Create hardened units**

Use the exact contracts above, plus:

```text
Requires=bp-postgres.service
After=bp-postgres.service
UMask=0077
TimeoutStartSec=2min
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bp-v2-forward-coverage
```

- [ ] **Step 4: Add CI validation**

Under the existing deployment validation block add:

```bash
python -m py_compile scripts/run_v2_forward_coverage.py
bash -n scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh
```

The Bash line is expected to fail until Task 5 adds the helper; keep Task 4 RED/GREEN scoped by first adding only the Python compile line, then add the Bash line with Task 5.

- [ ] **Step 5: Run GREEN unit/deployment tests**

Run:

```bash
pytest tests/features/test_v2_forward.py tests/features/test_v2_forward_cli.py tests/deploy/test_phase14_v2_forward_coverage_deployment.py -v
```

Expected: systemd tests pass except the Task 5 rollout-helper assertions, which must not be introduced until Task 5.

- [ ] **Step 6: Commit**

```bash
git add deploy/bp-v2-forward-coverage.service deploy/bp-v2-forward-coverage.timer tests/deploy/test_phase14_v2_forward_coverage_deployment.py .github/workflows/ci.yml
git commit -m "deploy: schedule V2 forward coverage collection"
```

---

### Task 5: Add exact-head rollout/acceptance with runtime rollback

**Files:**
- Create: `scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh`
- Modify: `tests/deploy/test_phase14_v2_forward_coverage_deployment.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: expected deployed head, exact candidate SHA, existing Phase 14 runtime installer patterns, systemd unit files, disk-health command, coverage CLI.
- Produces: Cloud Shell helper controlled by:

```text
PHASE14_V2_FORWARD_HEAD
PHASE14_V2_FORWARD_FROM_HEAD
PHASE14_V2_FORWARD_PROJECT
PHASE14_V2_FORWARD_ZONE
PHASE14_V2_FORWARD_VM
PHASE14_V2_FORWARD_BRANCH
PHASE14_V2_FORWARD_ENV_FILE
```

- [ ] **Step 1: Write RED rollout-contract tests**

Require the helper to contain all of these fail-closed checks:

```text
exact 40-character head validation
active gcloud account validation
current /opt/bp HEAD == PHASE14_V2_FORWARD_FROM_HEAD
fetched branch SHA == PHASE14_V2_FORWARD_HEAD
candidate descends from deployed head
allowed-path whitelist
frozen V1 feature/live_prediction/calibration/execution path checks
RESEARCH/live-disabled/zero-money before and after
all seven existing research services active before and after
disk health before and after
new service/timer installed and enabled
bounded manual oneshot execution succeeds
coverage future_cutoff_violation_count == 0
coverage policy_selected == false
coverage automatic_promotion == false
runtime rollback on post-switch failure
immutable market_features evidence is never deleted during rollback
sanitized evidence file written under /var/lib/bp/evidence/
```

- [ ] **Step 2: Run RED rollout tests**

Run:

```bash
pytest tests/deploy/test_phase14_v2_forward_coverage_deployment.py -v
```

Expected: only new helper/CI assertions fail.

- [ ] **Step 3: Implement minimal guarded helper**

Follow the established `phase14_v2_gate_a_rollout_cloudshell.sh` pattern. The allowed source diff may include only:

```text
PROJECT_STATE.json
docs/**
.github/workflows/ci.yml
src/bp_engine/features/v2_forward.py
src/bp_engine/features/v2_forward_cli.py
scripts/run_v2_forward_coverage.py
deploy/bp-v2-forward-coverage.service
deploy/bp-v2-forward-coverage.timer
scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh
tests/features/test_v2_forward.py
tests/features/test_v2_forward_cli.py
tests/deploy/test_phase14_v2_forward_coverage_deployment.py
```

Explicitly freeze:

```text
src/bp_engine/features/service.py
src/bp_engine/live_prediction
src/bp_engine/calibration
src/bp_engine/execution
```

Install the new unit files to `/etc/systemd/system/`, daemon-reload, enable the timer, and run the oneshot once explicitly for acceptance.

Rollback may restore `/opt/bp` and prior unit/timer files/service-enable state, but must never DELETE FROM or truncate `market_features` or any research ledger.

- [ ] **Step 4: Add final CI Bash syntax gate**

Add:

```bash
bash -n scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh
```

- [ ] **Step 5: Run rollout contract and shell syntax GREEN**

Run:

```bash
pytest tests/deploy/test_phase14_v2_forward_coverage_deployment.py -v
bash -n scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh tests/deploy/test_phase14_v2_forward_coverage_deployment.py .github/workflows/ci.yml
git commit -m "deploy: guard V2 forward coverage rollout"
```

---

### Task 6: Full verification, documentation checkpoint, and draft PR

**Files:**
- Modify: `PROJECT_STATE.json`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md`
- Modify: `docs/superpowers/plans/2026-09-02-phase-14-v2-forward-coverage-collector.md`

**Interfaces:**
- Consumes: all prior task commits and CI evidence.
- Produces: review-ready branch and draft PR; no production rollout yet.

- [ ] **Step 1: Run full local/CI-equivalent verification**

Run:

```bash
ruff check .
pytest -q
python -m py_compile scripts/run_v2_forward_coverage.py
bash -n scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh
docker compose --env-file deploy/bp.env.example -f docker-compose.prod.yml config -q
python -m bp_engine.health
```

Dashboard validation:

```bash
cd apps/dashboard
npm test
npm run typecheck
npm run build
```

Expected: all pass.

- [ ] **Step 2: Re-verify frozen safety paths against `main`**

Require no diff in:

```text
src/bp_engine/features/service.py
src/bp_engine/live_prediction/**
src/bp_engine/calibration/**
src/bp_engine/execution/**
```

Also verify no migration file, wallet/secret path, risk-limit change, geography bypass, V2 policy threshold, `min_edge`, Phase 15 implementation, or live activation appears in the branch diff.

- [ ] **Step 3: Update docs to implementation-under-review**

Record:

```text
Gate A production rollout PASS remains canonical.
Forward collector implementation is code/test complete, not yet deployed.
No freshness/timing/model/calibration/edge policy selected.
No labels/outcomes joined.
Production collector rollout is separately gated.
Live false, zero-money, Phase 15 blocked.
```

Do not claim continuous coverage exists until host rollout actually passes.

- [ ] **Step 4: Run exact-head GitHub CI and PR-triggered gates**

Push the final branch head, require the normal CI plus Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak to pass on that exact SHA.

- [ ] **Step 5: Open a draft PR**

Suggested title:

```text
Phase 14: collect continuous outcome-blind V2 forward coverage
```

PR body must state the Gate A production evidence, collector-only scope, exact epoch/grace, TDD evidence, frozen V1 paths, no policy selection, no label/outcome access, no deployment yet, and the separate rollout boundary.

- [ ] **Step 6: Stop at deployment boundary**

Do not merge/deploy unless separately proceeding through review/merge and then an explicitly guarded research-only host rollout. Never treat the collector installation as Gate B authorization.
