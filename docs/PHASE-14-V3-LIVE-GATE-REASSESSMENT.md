# Phase 14 — Frozen V3 Live-Gate Reassessment

This runbook collects fresh **read-only** evidence for the exact frozen V3 after the user authorized pursuing a controlled live transition. It does not enable live trading and does not change the Master live gate.

## Command

From an authenticated Google Cloud Shell with a clean checkout exactly at current `origin/main`:

```bash
cd ~/BP
git fetch origin
git switch main
git pull --ff-only origin main
bash scripts/deploy/phase14_v3_live_gate_reassessment_cloudshell.sh
```

The helper fails locally unless the checkout is clean and exactly current. It then connects to the accepted `bp-recorder` VM and verifies the deployed checkout and the frozen V3/V4 runtime symlinks before reading evidence.

## Evidence collected

The output includes:

- V3-only settled-trade count, wins/losses, win-rate Wilson interval;
- realized paper P&L, deterministic bootstrap mean-P&L interval, profit factor;
- maximum realized-P&L drawdown and losing streak;
- largest-winner concentration and P&L with the largest winner removed;
- V3-only calibration means;
- V3 order/fill/settlement reconciliation;
- current RESEARCH/live-disabled/zero-money runtime state;
- current live-order-ledger row counts where the schema is available;
- official SDK import health without constructing a client;
- a direct request from the production VM to exactly `https://polymarket.com/api/geoblock`;
- the frozen V3 final-holdout reference for context.

## Non-negotiable safety boundary

The helper:

- forces PostgreSQL `default_transaction_read_only=on` and verifies it;
- creates no production file or database row;
- changes no service, timer, symlink, or checkout;
- does not read wallet/private-key environment variables;
- does not construct an authenticated Polymarket client;
- does not submit, cancel, sign, or reconcile a real order;
- does not change live-trading or money limits;
- does not mutate the Master live gate;
- does not authorize Phase 15.

A `PHASE14_V3_LIVE_GATE_REASSESSMENT=PASS` token means only that the read-only collection completed under its guards. It is **not** a live-trading PASS.

If the direct geoblock result is `blocked=true` or the check errors, geographic eligibility remains blocked/fail-closed. Do not route, proxy, VPN, relocate, or otherwise bypass the restriction.

After the output is reviewed, any Master live-gate status change must be a separate source-truth decision. V4 collection continues unchanged throughout.
