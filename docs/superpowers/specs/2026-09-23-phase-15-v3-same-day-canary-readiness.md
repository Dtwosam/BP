# Phase 15 — Frozen V3 Same-Day Canary Readiness v1

**Date:** 23 September 2026  
**Status:** frozen before new prospective calibration-reliability diagnostics are read  
**Mode:** RESEARCH; live trading disabled; real-money limits zero

## 1. Purpose

The user has explicitly requested the shortest compliant path to a controlled frozen-V3 live canary. This contract attempts to resolve the remaining statistical Master live-gate rows without changing the frozen V3 model, timing, calibration fit, edge threshold, or paper sizing and without weakening or bypassing geographic compliance.

This contract does **not** authorize live activation. It authorizes one new **read-only** accelerated readiness audit only.

## 2. Frozen V3 identity

```text
model_sha256       = 124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7
prediction_version = v3-frozen-paper-v1
selection_sha256   = a088c61b291a67866af1c565ee936d9761e6a2936b49f121f616081284dcd508
candidate          = single_feature_btc_logistic
offset_seconds     = 240
min_edge           = 0.075
```

No refit, recalibration fit, threshold search, timing change, feature change, side/regime filter, or paper-sizing change is permitted.

## 3. Sample-sufficiency rule

The project already froze the Phase 13 principle that there is no magic market/trade count and that uncertainty must decide whether prospective economics are informative.

Therefore the live-paper sample row passes only when:

- at least one settled frozen-V3 paper trade exists;
- at least one frozen-V3 prediction evaluation exists; and
- the deterministic 10,000-resample 95% bootstrap lower bound for **mean realized after-cost paper P&L** is strictly above zero.

No new round-number minimum is introduced after seeing the current paper sample.

## 4. Walk-forward stability rule

The original V3 Gate B successor was frozen before its outcomes were consulted. It required:

- exactly five chronological ordinary folds;
- at least 8 validation trades in every fold;
- at least 4 of 5 validation folds with nonnegative after-cost P&L;
- positive aggregate validation after-cost P&L.

The implementation forces the final policy to `no_trade` when that ordinary validation-economics gate fails. The immutable selected policy is instead `trade_threshold` with `min_edge=0.075`; therefore the frozen ordinary validation economics gate passed.

For this Master gate row, stability passes only if all three independent layers are positive:

1. the pre-registered five-fold ordinary validation economics gate passed;
2. the untouched frozen V3 final holdout has positive after-cost P&L;
3. prospective frozen-V3 paper mean-P&L bootstrap 95% lower bound is strictly above zero.

No holdout or paper result may be used to alter V3 after this mapping.

## 5. Calibration-acceptance rule

The existing 519-evaluation Brier/log-loss summaries are known, but the following **new reliability diagnostics have not yet been read** when this rule is frozen:

- 10-bin expected calibration error (descriptive only);
- calibration intercept from logistic calibration regression;
- calibration slope from logistic calibration regression;
- deterministic bootstrap 95% intervals for intercept and slope.

Calibration passes only if:

1. prospective calibrated Brier mean is no worse than the frozen pre-paper holdout Brier `0.10943703117284813`;
2. prospective calibrated log-loss mean is no worse than the frozen pre-paper holdout log loss `0.35419212970900277`;
3. the prospective calibration-intercept 95% interval contains `0`;
4. the prospective calibration-slope 95% interval contains `1`.

The intercept/slope bootstrap uses exactly 2,000 resamples with seed 15. ECE is reported but is not used as an after-the-fact threshold.

If this audit fails, calibration remains blocked; the acceptance rule must not be weakened after seeing the diagnostics.

## 6. Geography remains an independent hard gate

A statistical PASS cannot override geography.

Before any real order:

- the user's ordinary physical-network check must be unblocked with VPN/proxy disabled;
- the execution host itself must return `blocked=false` from the official direct Polymarket geoblock endpoint;
- the deployment must not be used to disguise a physically restricted user;
- VPN, proxy, tunnel, or other geographic-circumvention techniques are forbidden.

A blocked user location or execution host keeps the Master live gate closed.

## 7. Safety boundary

The accelerated audit:

- uses PostgreSQL read-only transactions;
- creates no production files;
- changes no production checkout;
- changes no services or timers;
- reads no wallet/signing material;
- constructs no authenticated trading client;
- submits no real order;
- does not create an activation manifest;
- keeps `LIVE_TRADING_ENABLED=false`;
- keeps real-money limits at zero;
- keeps V4 collection unchanged.

A statistical audit PASS means only that the statistical rows may be refreshed. A separate exact-SHA canary activation package and a complete Master-gate PASS are required before real money.
