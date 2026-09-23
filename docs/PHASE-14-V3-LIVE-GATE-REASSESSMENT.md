# Phase 14 — Frozen V3 Live-Gate Reassessment

**Status: completed one-shot read-only production reassessment on 23 September 2026. Do not rerun from current main absent a separately versioned reason.**

The completed run collected fresh **read-only** evidence for the exact frozen V3 after the user authorized pursuing a controlled live transition. It did not enable live trading. Its evidence-backed closeout updated only the Master gate status rows; no production or money mutation occurred.

## Historical one-shot command

The following command was used for the completed run on exact main `ba98b3871e03895d04bb2b06d4be5350f6c17491`. The helper is intentionally source-truth-version-bound and should now fail from later current main rather than silently rerun:

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
- V3-only calibration means across all evaluated frozen-V3 predictions, including no-trade predictions;
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


## Completed result

Sanitized evidence is `docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json`.

- 76 settled frozen-V3 paper trades: 47 wins / 29 losses.
- realized after-cost paper P&L: `+682.252111761097` USD;
- deterministic bootstrap 95% interval for mean P&L: `[+1.6407501892525114, +18.945890261783955]` USD;
- profit factor: `6.262572772140266`;
- max drawdown: `27.974134608836` USD;
- P&L excluding the largest winner: `+450.802786408456` USD;
- reconciliation: `OK`, zero violations;
- direct official geoblock: `blocked=true`, `US/SC`.

Master gate refresh: profitability `pass`, execution/reconciliation `pass`, explicit user authorization `pass`; sample sufficiency, calibration acceptance, and walk-forward stability `insufficient_evidence`; geographic compliance `fail`; overall live gate `fail`. Phase 15 remains blocked.
