# Phase 2 Pre-Host Checkpoint

**Date:** 21 August 2026  
**Phase:** 2 — 24/7 raw recorder  
**Status:** Pre-host gates green; 24-hour host soak still required  
**Live trading:** Disabled

## Verified checkpoint

The Phase 2 recorder foundation has passed the pre-host validation gates on commit `cf85c9139cfd887188bb10b60d6a75cf98e0e389`.

GitHub Actions results:

- CI: passed;
- Live Recorder Smoke: passed;
- Recorder Short Soak: passed.

CI verified:

- Ruff clean;
- 74 automated tests passed;
- deployment shell scripts parse successfully;
- production Docker Compose configuration validates;
- health command remains in `research` mode with live trading disabled.

The successful short PostgreSQL soak recorded 17,506 events in its measured window:

- Polymarket market stream: 15,109;
- Bybit linear/perpetual: 1,411;
- Bybit spot: 732;
- Coinbase spot: 254.

The health report returned `passed: true` with no failures. Each feed recorded one `disconnected` incident because the CI harness deliberately terminates the recorder after the short soak window.

Sanitized machine-readable evidence is stored at:

`docs/evidence/phase-2-prehost-short-soak.json`

## Recorder coverage now implemented

- recurring Polymarket BTC market discovery and rotating CLOB subscriptions;
- immutable Polymarket raw market events;
- Bybit spot order-book/trade feed;
- Bybit perpetual order-book/trade/ticker/liquidation feed;
- Coinbase secondary spot ticker/trade feed;
- PostgreSQL persistence;
- reconnect/backoff incidents;
- stale/recovered feed detection;
- clock-skew detection for events with transport-relevant source timestamps;
- queue backpressure incidents;
- graceful SIGTERM shutdown;
- host NTP requirement;
- safe research-mode defaults.

Polymarket `book` snapshot timestamps remain preserved in the immutable payload but are not misclassified as WebSocket transport timestamps.

## Always-on deployment package

Commit `cf85c9139cfd887188bb10b60d6a75cf98e0e389` also contains the Phase 2 deployment package:

- private localhost-only PostgreSQL Compose service;
- root-owned database lifecycle systemd unit;
- unprivileged `bp` recorder systemd unit with no Docker-socket access;
- on-host secret generation and protected environment file;
- Chrony/NTP enforcement;
- restart-on-failure behavior;
- formal 24-hour soak report command and evidence directory;
- operator deployment/recovery runbook.

See `docs/PHASE-2-DEPLOYMENT.md`.

## Remaining Phase 2 gate

Phase 2 is **not complete** yet.

The remaining acceptance gate is to deploy one immutable green Phase 2 commit on an always-on Ubuntu host and collect a genuine 24-hour observation window.

After the 24-hour run:

1. run `scripts/deploy/phase2_soak_report.sh`;
2. inspect the JSON evidence and recorder journal for unexplained gaps/restart loops;
3. commit sanitized 24-hour evidence;
4. update `PROJECT_STATE.json` and `docs/CHANGELOG.md`;
5. write the Phase 2 closeout note;
6. only then start Phase 3 retention and aggregation.

No model training, paper execution, or real-money trading should begin before the documented build order reaches those phases.
