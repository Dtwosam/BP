# Phase 4 — Historical Backfill Deployment

**Phase:** 4 — reproducible historical market and BTC reference data  
**Live trading:** Disabled

Phase 4 adds bounded, reproducible historical backfills without changing the Phase 2 recorder or Phase 3 retention behavior. Historical observations are stored separately from high-rate recorder tables and carry durable run/artifact provenance.

## Safety invariants

The host acceptance gate requires:

```text
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

Phase 4 does not place orders, enable live trading, delete recorder raw data, truncate tables, or run `VACUUM FULL`.

The known Phase 3 damaged raw interval remains excluded from any future raw-dependent research dataset:

```text
2026-08-22T20:00:00Z <= received_at < 2026-08-22T21:00:00Z
```

Exactly 250,000 raw rows are known missing from that interval. Do not attempt to reconstruct them from untrusted data.

## Historical sources and semantics

### Polymarket market metadata

Phase 4 uses the Gamma market API and the existing strict BTC Up/Down parser/rules fingerprint. Historical discovery is bounded by explicit UTC start/end parameters and configured horizons. Any rules fingerprint change remains fail-closed through `RuleChangeDetected`.

Raw market payloads are versioned in `polymarket_market_snapshots` by condition plus payload SHA-256.

### Polymarket token prices

Phase 4 uses the CLOB `/prices-history` endpoint independently for the Up and Down token IDs. Values are stored as exact decimals in `polymarket_price_history`.

This is a historical token-price series. It is not treated as historical order-book depth or as proof of an executable bid/ask at a timestamp.

### Historical Polymarket L2 limitation

No verified current free first-party historical order-book endpoint is used in Phase 4. The current public API surface does not provide a sufficiently reliable historical L2 contract for this build. The system therefore records this as unavailable instead of synthesizing book depth or silently substituting token prices for order books.

### Bybit BTC candles

Phase 4 implements Bybit V5 market-klines backfill for `BTCUSDT` spot and linear markets. Requests are split into deterministic non-overlapping half-open windows with at most 1,000 candles per request. Reverse-ordered responses are normalized to ascending UTC before exact-decimal storage.

Bybit's official V5 integration guidance states that IP addresses located in the United States are restricted and return HTTP 403. Both US-hosted GitHub runners and the production `us-east1` recorder VM exhibit that documented response. Phase 4 therefore treats Bybit historical REST as an **optional, audited source** on restricted hosts; it does not route around the provider restriction.

A Bybit HTTP 403 is classified as `BybitHistoryUnavailableError`. In the standard multi-source backfill it is persisted in `historical_backfill_runs` as `status=unavailable`, with zero inserted/existing/chunk counts and the HTTP 403 reason. Any different Bybit error remains a real failure. Use `standard --require-bybit` when running from an environment where Bybit history is mandatory and permitted.

This historical limitation does not rewrite the Phase 2 live-recorder record. Live Bybit feed evidence remains separate from Phase 4 REST backfill provenance.

### Coinbase BTC candles

Phase 4 uses the Coinbase public product-candles endpoint for `BTC-USD`. Requests are bounded to at most 350 buckets. Missing/no-tick intervals remain missing; Phase 4 does not invent candles or interpolate gaps.

## Data integrity model

Historical natural keys are immutable:

- Polymarket price: `(asset_id, observed_at, fidelity_minutes)`
- BTC candle: `(source, market_type, symbol, interval_seconds, bucket_at)`

An identical rerun returns `created=false` and does not duplicate data. If a provider returns a different historical observation for an already-stored natural key, the repository raises `HistoricalDataConflict` instead of silently rewriting the research record.

Each requested dataset also receives a `historical_backfill_runs` record. Network responses used for ingestion are represented in `historical_backfill_artifacts` with source, dataset, exact request parameters, download time, row count, and canonical response SHA-256. Revised responses for the same request can therefore be preserved as separate artifact versions.

Terminal run statuses used by Phase 4 are:

- `success` — the requested dataset was fetched and committed;
- `unavailable` — currently limited to the explicitly classified Bybit HTTP 403 environment restriction;
- `failed` — every other source, parsing, conflict, database, or operator failure.

## Operator commands

All data windows must be explicit and timezone-aware.

Run the standard Phase 4 sequence:

```bash
sudo -u bp /opt/bp/.venv/bin/python /opt/bp/scripts/historical_backfill.py standard \
  --start 2026-08-24T18:00:00Z \
  --end 2026-08-24T19:00:00Z \
  --env-file /etc/bp/bp.env
```

The standard sequence is fixed:

1. Polymarket market metadata;
2. Polymarket Up/Down token prices;
3. Bybit BTCUSDT spot candles when accessible;
4. Bybit BTCUSDT linear candles when accessible;
5. Coinbase BTC-USD spot candles.

The standard command continues only for the narrowly classified Bybit HTTP 403 condition. To require Bybit, add:

```bash
--require-bybit
```

Individual source commands remain strict; requesting Bybit directly does not silently convert failure into success.

```bash
python scripts/historical_backfill.py polymarket-markets --start ... --end ...
python scripts/historical_backfill.py polymarket-prices --start ... --end ...
python scripts/historical_backfill.py btc-candles --source coinbase-spot --start ... --end ...
python scripts/historical_backfill.py btc-candles --source bybit-spot --start ... --end ...
```

Use the production env file on the recorder host.

## Public-runner smoke

The dedicated GitHub Actions workflow checks a recent resolved BTC Up/Down market, both Polymarket token-price histories, recent Coinbase candles, and Bybit when accessible from the runner.

```bash
python scripts/historical_backfill_smoke.py
```

A restricted runner may report:

```text
status=environment_limited
bybit.status=environment_blocked_http_403
```

That is transparent source-availability evidence. The smoke must still prove non-empty Polymarket and Coinbase core results.

## Production host acceptance

Run the acceptance candidate from an isolated worktree so the active `/opt/bp` recorder checkout is not replaced during the gate. The checked-in acceptance script supports `BP_REPO=<candidate-worktree>` and uses the deployed `/opt/bp` checkout only for existing recorder/storage-health operations.

The host gate:

1. confirms the exact candidate SHA;
2. confirms trading is disabled and trade/loss limits are zero;
3. confirms the recorder is active and both Phase 3 storage timers remain enabled;
4. applies the additive/idempotent Phase 4 migration;
5. runs the historical source smoke and requires non-empty Polymarket + Coinbase core data;
6. accepts Bybit only as either verified `ok` or explicit `environment_blocked_http_403`;
7. runs the standard one-hour backfill twice;
8. requires non-empty core coverage on run 1 and existing core coverage on run 2;
9. requires the second run to insert zero historical observations;
10. requires exactly ten terminal dataset run records across the two standard runs;
11. permits `unavailable` only for Bybit spot/linear with an HTTP 403 reason and zero row/chunk counts;
12. records BTC/Polymarket coverage summaries;
13. requires post-run disk status `ok` and recorder activity;
14. checks for fatal recorder errors during the acceptance window;
15. verifies the Phase 3 forensic evidence SHA remains unchanged;
16. writes evidence below `/var/lib/bp/evidence/phase4-historical-backfill/<UTC timestamp>/`.

The default acceptance data window is:

```text
2026-08-24T18:00:00Z <= t < 2026-08-24T19:00:00Z
```

It is safely in the past and does not overlap the known Phase 3 damaged raw interval. The window can be overridden with `PHASE4_ACCEPTANCE_START` and `PHASE4_ACCEPTANCE_END`, but both must remain explicit timezone-aware values.

## Acceptance criteria

Phase 4 can close only when all of the following are true:

- normal CI passes, including PostgreSQL 16 migration/rerun coverage;
- public-runner historical smoke produces sanitized evidence without hidden command failure;
- production host proves non-empty Polymarket market/token history and Coinbase BTC history;
- Bybit host behavior is either successfully fetched or explicitly classified/audited as the documented HTTP 403 restriction;
- the first bounded standard backfill has non-empty core coverage;
- an immediate rerun inserts zero historical observations and reuses the core observations;
- every host run is terminal `success` or the narrowly allowed Bybit `unavailable` state;
- recorder remains active with no fatal error signature during the gate;
- Phase 3 storage timers remain enabled;
- disk status remains `ok`;
- Phase 3 forensic evidence checksum is unchanged;
- historical limitations above are preserved in durable documentation.

Only after those checks should `PROJECT_STATE.json`, `docs/CHANGELOG.md`, `docs/DECISION-LOG.md`, and the Phase 4 PR be closed out. Phase 5 is the official outcome/label pipeline; do not skip directly to feature/model development.

## Failure behavior

Unexpected source errors, changed natural-key values, failed migration, unclassified source-smoke states, recorder health regression, or disk-health regression block Phase 4 closeout.

Do not troubleshoot a Phase 4 failure by deleting historical or recorder raw rows. Do not disable conflict detection. Do not reinterpret unavailable historical L2 as token-price history. Do not route around Bybit's documented jurisdiction/IP restriction. Preserve failed run/evidence and fix the underlying source, schema, or code issue before rerunning the gate.
