# Phase 2 Closeout — 24/7 Raw Recorder

**Date:** 23 August 2026  
**Phase:** 2 — 24/7 raw recorder  
**Status:** Complete; Phase 3 retention and aggregation may begin  
**Live trading:** Disabled

## Verdict

Phase 2 passes its build-order acceptance gate:

- a genuine 24-hour always-on host soak completed;
- all four required feeds recorded throughout the window;
- there were no unexplained data gaps;
- stale periods, disconnects, errors, reconnects, and recoveries were persisted and reviewed;
- systemd reported zero recorder restarts during the run;
- no `clock_skew` or internal `backpressure` incidents occurred;
- every recorded disconnect had a subsequent `connected` incident.

The accepted frozen window is:

- start: `2026-08-22T20:12:57.033984Z`
- end: `2026-08-23T20:12:57.033984Z`
- duration: `86,400` seconds

Sanitized machine-readable evidence is stored at:

`docs/evidence/phase-2-host-soak-20260823.json`

## Evidence chronology

The first host attempt is **not** counted as passing evidence. It stopped after roughly 12 hours 38 minutes when the original 40 GB disk filled. The disk was recovered and expanded to 100 GB, and a logical dump of the failed-run database was preserved before the clean rerun.

The successful 24-hour capture ran on recorder commit:

`cd150668a6189467d4b663c719f8b33f576f5791`

The original formal report for the successful run was preserved unchanged and checksum-protected. It returned `passed: false` only because Bybit spot and linear each appeared to have an unresolved stale state.

That failure exposed a real incident-bookkeeping defect: the WebSocket runner called `FeedWatchdog.observe()` when a new socket connection started. If the feed had previously been marked stale, that startup call cleared stale state and returned a `recovered` incident, but the return value was ignored. When real messages then resumed, there was no stale state left to clear, so no `recovered` row was persisted.

The defect was reproduced test-first, fixed, and verified. Final post-soak reliability/evaluator commit:

`8c4c35b654b46a8bd8235daa2a03d43496693c2a`

On that commit:

- CI run 79 passed;
- Ruff was clean;
- 77 automated tests passed;
- Live Recorder Smoke run 48 passed;
- Recorder Short Soak run 16 passed.

The soak evaluator was also extended to support a fixed historical `--end-at`, allowing the exact original 24-hour window to be re-evaluated without collecting a replacement 24-hour dataset.

## Frozen-window result

Revalidation of the exact same window returned:

- `passed: true`
- `failures: []`

Event counts:

- Polymarket market: 37,762,194
- Bybit linear/perpetual: 4,074,066
- Bybit spot: 2,707,330
- Coinbase spot: 1,126,086
- total: 45,669,676

The revalidation sees 223 more receive-timestamped events than the original immediate report because those events were received inside the frozen window but committed by the writer shortly after the first report query. The window boundaries themselves are unchanged.

The revalidated on-host report is checksum-protected with SHA-256:

`a9f5b13a26da1d657f33c92653221d019bffa4af0849c3886e18a9c33f1e20eb`

The original failed formal report remains preserved with SHA-256:

`8a41df56bdd3a270e49f982086f183211f326682cd3905e7df53d3c30a53b3c1`

The failed first-attempt database dump remains preserved with SHA-256:

`80c867ee2458ed0a2684658bd23f46ec09df7372c574e42f343f04acefcd0e59`

## Continuity review

Systemd state for the successful run:

- `Result=success`
- `NRestarts=0`
- process start: `2026-08-22 20:06:16 UTC`
- process exit: `2026-08-23 20:21:09 UTC`

The process therefore started before the formal window, remained the same process through the full window, and exited only after the operator stopped it after evidence capture.

The recorder unit directs stdout/stderr to journald, but the application does not emit routine log lines during normal operation. The exact-window journal query returned no entries. Reliability review therefore used the persisted `feed_incidents` records plus systemd process state.

Disconnect continuity:

- Bybit linear: 1 disconnect, maximum reconnect 0.799 s, 0 unresolved
- Bybit spot: 1 disconnect, maximum reconnect 0.835 s, 0 unresolved
- Coinbase spot: 2 disconnects, maximum reconnect 0.267 s, 0 unresolved
- Polymarket market: 31 disconnects, maximum reconnect 0.444 s, 0 unresolved

The two Bybit stale periods that triggered the original false formal failure were explicitly bounded by raw data:

- Bybit spot: 30.734 s raw-event gap, then ingestion resumed
- Bybit linear: 51.229 s raw-event gap, then ingestion resumed

These gaps align with persisted stale/error/reconnect/connected incidents and are therefore explained, not silent gaps.

Polymarket recorded one server-side `1013` slow-consumer disconnect (`send buffer full`) plus transient keepalive/no-close-frame disconnects. All recorded disconnects subsequently reconnected; no internal recorder `backpressure` incident was recorded.

## Storage lesson carried into Phase 3

The failed first host attempt demonstrated that persisting every high-rate raw update indefinitely is not sustainable on the current host. Polymarket order-book `price_change` events dominate storage volume and represent book-level deltas, not trade flow.

Phase 3 must therefore address the already-planned retention work before extended raw accumulation:

- bounded hot PostgreSQL retention;
- full raw compressed/archive strategy where justified;
- compact derived market-state intervals;
- partition/index strategy;
- measured disk growth per day;
- disk-space alerting.

This is a Phase 3 concern and does not retroactively invalidate the Phase 2 recorder acceptance result.

## Phase transition

Phase 2 is closed.

The next permitted build-order work is **Phase 3 — retention and aggregation**.

Historical backfill, labels, features, model training, paper trading, and live trading remain later phases. `LIVE_TRADING_ENABLED` remains false.
