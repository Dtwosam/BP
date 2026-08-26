# Phase 10 Prospective Live Prediction Deployment

Phase 10 is a **research-only, money-disabled prospective prediction service**. Its acceptance gate proves that the frozen Phase 9 policies can be applied to future verified markets before outcome, with immutable prediction evidence and delayed official-outcome evaluation. This is **not a profitability claim** and it does not authorize paper or live execution.

## Frozen production research sources

Phase 10 V1 accepts exactly these immutable Phase 9 policy sources:

- 5m: `phase9-300-c9f0e00eb7836af08008c66909f8f179`
  - semantic SHA-256: `c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24`
- 15m: `phase9-900-15c234f25588b23cce73a12f87a2e2ea`
  - semantic SHA-256: `15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd`

No other calibration run, horizon, semantic hash, or post-hoc policy selection is accepted by V1.

## Safety boundary

Before the predictor starts, the host gate requires `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. In other words, **live trading disabled** is a hard startup condition.

A stored `trade=true` value is a **research decision only**. It records what the frozen research edge policy would have selected from the observed evidence. There is **no order** submission, account action, fill generation, or position-management path in Phase 10.

The `bp-live-predictor` process runs as the unprivileged `bp` user with systemd hardening. The production acceptance helper also runs the exact candidate export under `bp`; root is used only for host orchestration and service management.

## Candidate provenance

Never accept a moving branch tip. The operator supplies an exact candidate SHA in `PHASE10_HEAD`. The Cloud Shell wrapper fetches `build/phase-10-live-prediction-engine`, verifies that the branch still resolves to that SHA, creates a detached root-owned worktree, and exports it through `git archive` into a separate bp-owned source directory.

The host gate then receives the exact verified worktree SHA as `BP_VERIFIED_HEAD` and refuses to run unless it matches its expected SHA. The Git worktree is not re-owned and no global Git trust configuration is added.

Candidate source and virtualenv runtime artifacts live below `/var/lib/bp/phase10-runtime`, not `/tmp` or `/var/tmp`, so the hardened predictor service can still access them while `PrivateTmp=true` remains enabled.

## Disconnect-resilient host acceptance

The Cloud Shell wrapper treats the long prospective acceptance as a VM-owned job rather than an SSH-owned process. After candidate verification/export, it starts a transient systemd oneshot on `bp-recorder`; that job runs `phase10_host_acceptance.sh` and writes the authoritative console evidence directly to `/var/lib/bp/evidence/phase10-host-acceptance-latest.log` on the VM.

A Cloud Shell or SSH disconnect therefore does **not** make an in-flight host proof valid or invalid by itself. Re-running the same pinned helper for the same candidate reattaches to the VM-owned acceptance job and reports its current/final state instead of starting an unverified replacement proof. A run is accepted only from the VM evidence and only when the final required verdict tokens are present.

If the wrapper loses its client connection before the host job finishes, do not infer success from service inactivity, elapsed time, or partial evidence. Reconnect with the exact same pinned candidate helper and read the VM-owned result.

## Prospective evidence only

Phase 10 acceptance is intentionally **prospective**. The gate starts the candidate predictor and observes **future verified markets**. It waits for new 5m and 15m prediction opportunities and requires at least one newly recorded pre-outcome prediction for each horizon within the bounded observation window.

Predictions are accepted only when their recorded timing is within the frozen schedule deadline. Post-scheduled price or book observations are integrity violations. A missed deadline becomes one of the gate's **honest misses**; the acceptance process never repairs it by creating a late prediction or by substituting historical replay.

If the bounded observation window contains no future verified opportunity for a required horizon, the gate reports `PHASE10_HOST_ACCEPTANCE=PENDING` rather than manufacturing evidence. Likewise, an official label that has not arrived remains **evaluation pending**. Evaluation evidence is appended only when the canonical official label already exists.

## Machine-readable evidence

The host gate writes evidence under `/var/lib/bp/evidence/phase10-live-prediction/<UTC stamp>/` and the Cloud Shell wrapper mirrors the latest console evidence at:

`/var/lib/bp/evidence/phase10-host-acceptance-latest.log`

The final evidence includes at least:

- `PREDICTION_COUNT_5M` and `PREDICTION_COUNT_15M` for predictions recorded during the candidate observation window;
- `FUTURE_MARKET_COUNT_5M` and `FUTURE_MARKET_COUNT_15M`;
- `LATE_OR_MISSED_COVERAGE` for honest missed historical coverage visible to the read-only report;
- `MAX_LATENESS_MS`;
- `PRE_OUTCOME_VIOLATIONS`;
- `SOURCE_CUTOFF_VIOLATIONS`;
- `SEMANTIC_HASH_VIOLATIONS`;
- `DUPLICATE_NATURAL_KEYS`;
- `EVALUATION_MUTATION_VIOLATIONS`;
- `ORDER_SIDE_EFFECT_VIOLATIONS`;
- evaluation count/status, which may legitimately be `pending`;
- recorder and disk-health status before and after the proof.

A nonzero `trade=true` count is not required, and positive hypothetical P&L is not required. Phase 10 acceptance is a timing, provenance, immutability, operational-safety, and prospective-evidence gate; it is not an economic-performance gate.

## Run from Google Cloud Shell

First identify the exact candidate SHA that passed all required exact-head CI/smoke gates, then run the **pinned helper for that SHA**. Do not execute an older helper after the candidate head has changed.

The wrapper connects to the recorder VM, verifies/exports the exact candidate, and starts or reattaches to the VM-owned acceptance job. The default observation bound is 2100 seconds. For an operationally justified longer window, set `PHASE10_OBSERVE_SECONDS` explicitly before the first invocation; reducing it below 60 seconds is rejected.

A successful run ends with both:

```text
VERDICT=PASS
PHASE10_HOST_ACCEPTANCE=PASS
```

If future market evidence is unavailable, the gate ends pending instead of passing. If any timing, source-cutoff, semantic-integrity, duplicate-key, prediction-mutation, runtime-safety, recorder-health, disk-health, or execution-side-effect check fails, the gate fails closed.

## Interpreting the result

`PHASE10_HOST_ACCEPTANCE=PASS` means the exact candidate produced valid prospective research predictions with the frozen policy sources under the money-disabled safety boundary. It does not mean the strategy is profitable, and it does not change the repository rule that Phase 10 is research-only.

Any later proposal to enable paper or live execution requires a separate reviewed phase with its own economic evidence, risk controls, and explicit authorization. Phase 10 itself contains no execution permission.
