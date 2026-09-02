# Phase 14 — V2 Forward Coverage Collector Design

**Date:** 2 September 2026
**Status:** Implementation complete; review packaging; not deployed
**Phase:** 14 — research correction / evidence collection
**Mode:** RESEARCH only; live trading disabled; all real-money limits remain zero
**Canonical decision:** D-033

**Implementation checkpoint:** `af8b2e4f741851df1b055b57c3fc44e39c7b6b06`
**Pre-packaging verification:** CI #1978 / run `33639062997`, 860 tests, full Python/deployment/dashboard GREEN
**Production collector activation:** not performed; separately authorized after review/merge

## 1. Purpose

Gate A for timestamp-coherent 5m `market_price` V2 is now deployed and production-accepted. The next step is not model tuning or trading-policy selection. It is continuous, forward, outcome-blind collection of immutable `core-v2-last-trade` feature evidence so later research can choose any V2 freshness/timing policy from a sufficiently broad unlabeled sample rather than from the original V1 failures or the single Gate A acceptance market.

This subsystem exists only to keep collecting timestamp-coherent V2 research evidence across completed 5-minute BTC Up/Down markets.

## 2. Starting evidence

The Gate A rollout completed with:

- deployed old head: `be1f82f65d15b2e172495e6ae934ec9a78648c32`
- deployed new head: `d077e45f24704e6038c947169c84527e954de975`
- rollout start: `2026-09-02T12:18:02Z`
- host evidence: `/var/lib/bp/evidence/phase14-v2-gate-a-rollout-20260902T122655Z.txt`
- verdict: `PHASE14_V2_GATE_A_ROLLOUT=PASS`
- production mode: `research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- all seven research services active
- real Polymarket last-trade provenance demonstrated with dedicated provider timestamp, BP receipt timestamp, price, and dedupe identity
- generic market activity demonstrated not to refresh the dedicated last-trade timestamp
- one fully completed post-rollout 5m market generated exactly four immutable V2 rows at offsets 60/120/180/240 seconds
- future source cutoff violations: zero
- coverage reporter emitted `policy_selected=false` and `automatic_promotion=false`
- short recorder soak passed
- disk status remained `ok`

The first acceptance sample also showed why policy selection must remain deferred: selected-book age was approximately 0.4–1.1 seconds while observed last-trade source age reached roughly 18–20 seconds. This one-market sample is operational evidence only, not enough to choose a V2 freshness cutoff.

## 3. Scope

### In scope

Build a production research collector that repeatedly:

1. finds completed 5m markets starting at or after the Gate A rollout epoch;
2. identifies markets that do not yet have the complete four-row `core-v2-last-trade` set;
3. generates only the approved offsets 60, 120, 180, and 240 seconds;
4. preserves existing immutable rows and explicit missing-data evidence;
5. runs the existing outcome-blind V2 coverage report after successful generation work;
6. emits structured logs for generation and coverage state;
7. survives reboot/restart without needing a mutable cursor file.

### Out of scope

This work must not:

- read or join official outcomes or labels;
- calculate accuracy, P&L, Brier score, log loss, or profitability;
- choose a last-trade freshness cutoff;
- choose a V2 prediction timestamp policy beyond the already-fixed Gate A offsets;
- choose calibration, model, edge, or `min_edge` policy;
- create V2 live predictions;
- create V2 paper orders or settlements;
- modify V1 predictions, calibration, paper evidence, or P&L;
- modify the existing 10-second selected-book freshness contract;
- enable live trading or change real-money limits;
- begin Phase 15.

## 4. Recommended architecture

Use a hardened systemd **oneshot service + timer**, not a new long-running daemon.

### Why a timer

The underlying task is periodic database reconciliation, not low-latency trading. A timer is simpler to operate, easier to audit, and naturally restart-safe. The database itself is the checkpoint: if a timer cycle is missed, a later cycle discovers the same unfinished completed markets.

### Components

1. **`bp_engine.features.v2_forward`**
   Pure orchestration logic for eligible-target discovery and one collection cycle.

2. **CLI / script**
   A thin entrypoint that loads settings, enforces research-zero-money safety, opens the database transaction, runs one cycle, and prints deterministic JSON stats.

3. **`bp-v2-forward-coverage.service`**
   Hardened unprivileged oneshot unit running as `bp`.

4. **`bp-v2-forward-coverage.timer`**
   Runs the service every minute with a small boot delay and persistent catch-up semantics.

5. **Guarded rollout helper**
   Exact-head production installation/acceptance with rollback of runtime files/service state on failure while never deleting already-collected immutable research evidence.

## 5. Eligibility and data flow

The canonical V2 forward epoch is:

`2026-09-02T12:18:02Z`

A market is eligible only when all of the following are true:

- `horizon_seconds == 300`;
- `start_at >= 2026-09-02T12:18:02Z`;
- `end_at <= cycle_at - 15 seconds`;
- static identity fields required by `V2FeatureTarget` are valid;
- at least one of the four natural keys for `core-v2-last-trade` is absent.

The 15-second post-end grace is purely operational: it avoids racing the recorder/database immediately at market close. It does not change the feature timestamps, does not alter as-of cutoffs, and is not a V2 economic freshness threshold.

The collector must never generate features for an active market.

For each eligible market:

1. load only static target identity/window/token fields;
2. call the existing V2 feature generation path with `preserve_existing=true` semantics;
3. require the natural-key set to remain exactly the approved offsets;
4. leave unavailable source evidence explicit rather than filling or synthesizing it;
5. never rewrite an existing immutable row;
6. after the cycle, run the existing read-only V2 coverage reporter.

## 6. Checkpointing and restart behavior

No cursor file is required.

The collector derives pending work from immutable database state:

- completed post-epoch 5m target exists;
- expected V2 natural key does not yet exist.

This means:

- service restarts are safe;
- host reboots are safe;
- repeated timer runs are idempotent;
- a temporary outage can be recovered by a later cycle;
- already-written V2 rows remain the canonical checkpoint.

## 7. Safety boundary

Every cycle must fail closed unless all are true:

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`

The service must expose no wallet, signing, order-placement, promotion, or live-enable path.

The systemd unit should follow the repository's existing hardened research-service pattern:

- `User=bp`, `Group=bp`
- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `PrivateDevices=true`
- `ProtectHome=true`
- `ProtectSystem=full`
- restricted address families
- journal-only output

The collector may write only the existing immutable `market_features` V2 rows through the approved repository path. Coverage reporting remains read-only.

## 8. Outcome blindness

Target discovery and feature generation must not query outcome/label columns.

Tests must prove that the eligible-target SELECT includes only static market identity/window/token fields. The collector must not import label-generation, evaluation, calibration, paper-execution, or live-readiness modules.

Coverage remains limited to provenance/availability/freshness diagnostics already defined by Gate A. It must continue to emit:

- `policy_selected=false`
- `automatic_promotion=false`

## 9. Operational cadence

Default timer cadence: once per minute.

A one-minute cadence is sufficient because approved feature timestamps occur within 5-minute markets but materialization happens only after the market has completed. This service is evidence collection, not a trading decision path.

The timer should use persistent catch-up behavior so a missed scheduled run after reboot is retried automatically.

## 10. Error handling

A collection cycle should fail nonzero on:

- safety-boundary violation;
- malformed market identity/window data;
- unexpected V2 natural-key conflict;
- semantic immutable-row mismatch;
- database failure;
- future-cutoff violation detected in newly generated rows;
- coverage reporter invariant failure.

One bad market must not silently mutate prior evidence. Existing immutable rows remain preserved.

Operational failures should be visible through systemd/journal status and should be retryable on the next timer cycle after the underlying problem is corrected.

## 11. Testing requirements

TDD must prove at minimum:

1. active markets are never eligible;
2. pre-epoch markets are never eligible;
3. only 5m markets are eligible;
4. fully completed post-epoch markets with missing V2 keys are eligible;
5. markets with all four V2 keys are skipped;
6. partial markets generate only missing keys and preserve existing rows;
7. repeat cycles are idempotent;
8. only offsets 60/120/180/240 can be materialized;
9. target discovery remains outcome-blind;
10. future source cutoffs remain zero;
11. safety violations fail closed before writes;
12. coverage stays `policy_selected=false` / `automatic_promotion=false`;
13. systemd service is unprivileged/hardened;
14. timer cadence and persistent catch-up contract are explicit;
15. rollout/acceptance is exact-head guarded and rollback-capable;
16. V1 feature service, live prediction, calibration, execution, risk limits, and Phase 15 paths remain untouched.

## 12. Production acceptance

A production rollout is a separate operational step after code review and merge.

Acceptance must prove:

- deployed old/new head guards are correct;
- research-zero-money safety before and after;
- all existing core services remain active;
- new timer/service are installed, enabled as intended, and run successfully;
- at least one bounded collection cycle executes on production;
- no active market is materialized;
- V2 rows remain immutable/idempotent;
- coverage report remains outcome-blind with zero future-cutoff violations;
- disk health remains `ok`;
- sanitized evidence is written under `/var/lib/bp/evidence/`.

The rollout must not claim sample sufficiency or authorize Gate B merely because the collector is operational.

## 13. Gate after this work

Successful implementation and rollout only establish continuous forward V2 evidence collection.

The next research gate remains intentionally separate: once enough outcome-blind coverage exists, define and pre-register a finite candidate set for any V2 last-trade freshness/timing policy **before** joining labels or economic outcomes.

Until that later gate is explicitly authorized:

- no V2 policy is selected;
- no V2 model/calibrator/edge threshold is selected;
- no V2 paper execution begins;
- live trading remains disabled;
- Phase 15 remains blocked.
