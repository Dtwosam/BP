# Phase 14 Live Trading Readiness v1

Phase 14 builds and verifies the engineering controls required before any real-money execution can even be considered. It does **not** authorize live trading. The accepted operating boundary remains `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` unless a later Master live gate is satisfied and explicit user authorization is recorded.

## What Phase 14 acceptance proves

`PHASE14_HOST_ACCEPTANCE=PASS` is a **non-spending** engineering acceptance token. It proves that the exact candidate SHA can be installed on the production host, the required services remain active, the official SDK imports, the public geoblock endpoint can be read directly, the live execution gateway blocks synthetic submission before client construction under blocked settings, zero-limit risk rules fail closed, reconciliation can complete on isolated synthetic evidence, and the production live-order ledger has `REAL_ORDER_SIDE_EFFECTS=0` across the acceptance run.

It is not the Master live gate and must never be interpreted as permission to spend real money.

Required host tokens are:

- `CANDIDATE_HEAD=<sha>`
- `SERVICES_ACTIVE=PASS`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `SDK_IMPORT=PASS`
- `INTERLOCK_BLOCKS_SUBMISSION=PASS`
- `RISK_RULES=PASS`
- `RECONCILIATION=PASS`
- `GEOBLOCK_BLOCKED=true|false|error`
- `LIVE_GATE_ELIGIBLE=false`
- `REAL_ORDER_SIDE_EFFECTS=0`
- `PHASE14_HOST_ACCEPTANCE=PASS`

## Safety boundary

The host acceptance script must never construct the secure trading client and must never load, print, or pass wallet signing material. The only external network action in the host acceptance is the direct public geoblock read. Synthetic execution checks use an isolated SQLite ledger and a client factory that fails immediately if the gateway attempts to construct a client.

The production database is read only for before/after live-order ledger counts. A successful acceptance requires those counts to remain unchanged, producing `REAL_ORDER_SIDE_EFFECTS=0`.

The live gateway remains unavailable for production spending after this acceptance. Phase 14 diagnostics may report readiness evidence, but the application boundary still treats real execution as disabled.

## Exact-head CI gate

Before host acceptance, require the exact candidate SHA to have fresh green branch gates:

1. Ruff passes.
2. All Python tests pass.
3. Deployment asset validation passes, including both Phase 14 shell scripts and `scripts/run_live_readiness.py` compilation.
4. Health remains research/live-disabled.
5. Dashboard tests, typecheck, and build pass.
6. Live Recorder Smoke passes.
7. Recorder Short Soak passes.
8. Historical Backfill Smoke passes.

Do not use an older green run for a newer candidate SHA.

## Production host acceptance

From an authenticated Google Cloud Shell, set the SHA that is green across the required branch gates and run:

```bash
PHASE14_HEAD=<exact-green-sha> bash scripts/deploy/phase14_cloudshell_accept.sh
```

The helper fetches the named Phase 14 branch, verifies that it still resolves to the exact expected SHA, exports that exact tree into a bp-owned runtime directory, and runs the host acceptance through a disconnect-resilient systemd unit. `BP_ENV_FILE` is passed only as a file path; secret values are not passed as command arguments.

Record the sanitized host output only after the real host run exists. Do not fabricate missing tokens and do not record IP addresses, signing material, or other secrets.

## Geographic eligibility

`GEOBLOCK_BLOCKED` must come from the production host's direct public Polymarket geoblock response:

- `false`: geographic eligibility is not blocked by this one check, but all other Master live gate rows still apply.
- `true`: geographic eligibility is blocked.
- `error`: geographic eligibility is unknown and therefore fails closed.

If the production host is blocked or the geoblock check errors, **do not relocate** the workload to evade the result and **do not proxy** traffic around the restriction. Preserve the result as an explicit Master live gate blocker.

## Master live gate and Phase 15

Task 11 must build the Master live gate matrix from immutable evidence. Engineering acceptance alone is insufficient. The gate must separately evaluate historical reproducibility, leakage controls, time-ordered splits, stable walk-forward evidence, live paper sample size and uncertainty, positive after-cost profitability, calibration, risk/kill-switch tests, execution/reconciliation tests, geographic/compliance eligibility, and explicit user authorization.

If any row fails or has insufficient evidence, Phase 15 remains blocked. In particular, a tied or unconfirmed challenger is not proof of positive after-cost profitability. `PHASE14_HOST_ACCEPTANCE=PASS` can therefore coexist with `LIVE_GATE_ELIGIBLE=false`.

Only after every required Master live gate item is genuinely satisfied may the project record a Phase 15-ready state. Real-money activation still requires separate, explicit user authorization; building Phase 14 is not that authorization.
