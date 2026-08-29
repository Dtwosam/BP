# Phase 13 Research Improvement Loop

Phase 13 adds a deliberately conservative, append-only research loop around the accepted 5m Phase 9 champion. It does **not** enable live execution, change the paper/live policy pointer, or make a research result an automatic production promotion.

## Safety boundary

The Phase 13 acceptance path must run with all of these settings intact:

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`

The dashboard must continue to report `execution_available=false`. The existing recorder, PostgreSQL, dashboard API, dashboard web, and paper-execution services remain active throughout acceptance. Phase 13 acceptance does not stop or restart the recorder or PostgreSQL.

Database connectivity is loaded through `BP_ENV_FILE` and `get_settings()`. Do not place credentials or the database connection string on a process command line, in acceptance evidence, or in shell output.

## Frozen champion and challenger

The first Phase 13 challenger is restricted to the accepted 5m Phase 9 champion:

- Phase 9: `phase9-300-c9f0e00eb7836af08008c66909f8f179`
- Phase 8: `phase8-300-efdf493067e9d56419afc4d88452bec6`
- Phase 7: `phase7-300-0a822e17ceced11742bf6d3bc8214f44`

Host acceptance checks the complete immutable IDs and semantic SHA-256 chain before it evaluates anything.

The challenger adds a validation-selected max-spread abstention guard. It reuses the accepted Phase 9 calibration/min-edge mechanics and Phase 8 fold membership. Spread selection is made from development validation only; ordinary OOS and the legacy Phase 9 final holdout cannot participate in selection.

## Evidence roles

Phase 13 keeps evidence roles explicit:

- development evidence is used only for selection;
- ordinary OOS is diagnostic evaluation evidence;
- `fresh_holdout` and `prospective_paper` are the only independent confirmation roles;
- the accepted Phase 9 final holdout remains legacy evidence and cannot be relabeled as fresh confirmation.

The initial spread-guard adapter does not fabricate a new `fresh_holdout` or `prospective_paper` sample. Therefore an ordinary/legacy-only acceptance evaluation is expected to contain `independent_confirmation_missing` and to have `promotion_eligible=false`.

A positive average P&L delta is not sufficient for promotion. The frozen comparator also requires a positive 95% lower confidence bound, non-worse calibrated log loss and Brier score, independent confirmation, and intact evidence/provenance boundaries.

## Deliberate decisions

Promotion decisions are append-only research records. They do not modify a live or paper policy pointer.

When an evaluation is not `promotion_eligible`, attempting `promote_challenger` must fail closed. The safe Phase 13 acceptance decision is `keep_champion`, which preserves the accepted Phase 9 champion.

Host acceptance explicitly proves both behaviors:

1. an ineligible `promote_challenger` decision is rejected;
2. a `keep_champion` decision is recorded idempotently.

## CI verification

Before any host acceptance, the exact candidate head must be GREEN for:

```bash
ruff check .
pytest
bash -n scripts/deploy/phase13_host_acceptance.sh
bash -n scripts/deploy/phase13_cloudshell_accept.sh
python -m py_compile scripts/run_improvement.py
```

Dashboard tests, typecheck, and build must also be GREEN. The health check must still report research mode with live trading disabled.

## Host acceptance

Run host acceptance only against an exact commit SHA that has passed CI. The helper creates a detached exact-head candidate archive rather than changing the production checkout in place.

From Google Cloud Shell:

```bash
export PHASE13_HEAD=<verified-full-commit-sha>
bash scripts/deploy/phase13_cloudshell_accept.sh
```

The helper verifies that the remote branch still resolves to `PHASE13_HEAD`, creates an isolated candidate source tree, and launches a disconnect-resilient one-shot host acceptance job. `BP_ENV_FILE` is passed as a file path only; secrets are not copied into command arguments.

The host acceptance must verify:

- exact candidate SHA;
- research/live-money safety settings;
- exact accepted Phase 9 → Phase 8 → Phase 7 immutable chain;
- experiment registration idempotence;
- evaluation idempotence and semantic identity;
- `independent_confirmation_missing` when no new independent confirmation exists;
- blocked ineligible promotion;
- append-only `keep_champion` decision;
- dashboard `execution_available=false`;
- paper reconciliation status `OK` with zero violations;
- all five production services still active.

A successful run ends with:

```text
CHAMPION_CHAIN=PASS
EXPERIMENT_IDEMPOTENT=PASS
EVALUATION_IDEMPOTENT=PASS
PROMOTION_GUARD=PASS
DECISION=keep_champion
PROMOTION_ELIGIBLE=false
RECONCILIATION=PASS
SERVICES_ACTIVE=PASS
PHASE13_HOST_ACCEPTANCE=PASS
```

If any invariant fails, the script exits non-zero and must not emit `PHASE13_HOST_ACCEPTANCE=PASS`.

## Promotion meaning

`promotion_eligible=true` means only that a frozen research evaluation satisfied every Phase 13 gate. It is not authorization for live trading and does not change real-money limits. Any later production policy change requires a separate reviewed phase with its own exact-head acceptance and safety controls.
