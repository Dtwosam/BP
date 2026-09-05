# BTC Polymarket Prediction Engine — Build Order

**Version:** 0.1.0  
**Authority:** Subordinate to `MASTER-SOURCE-OF-TRUTH.md`.

This file tells a future chat or developer exactly what to build next.

---

## Operating rule

Never skip forward because a later phase looks more interesting.

For each phase:

1. implement;
2. test;
3. record evidence;
4. update `PROJECT_STATE.json`;
5. update the changelog;
6. only then move to the next phase.

---

## 0. Bootstrap the repository

Create the repository structure specified in the Master Source of Truth.

Minimum deliverables:

- Python project;
- test runner;
- linter/formatter;
- `.env.example`;
- `.gitignore`;
- Docker/Docker Compose baseline;
- PostgreSQL local service;
- config loader;
- structured logging;
- health command;
- CI if GitHub is used.

Acceptance:

- fresh clone can start the local stack from documented commands;
- tests pass;
- no secrets are committed.

---

## 1. Build Polymarket market discovery first

Why first: the whole project ultimately trades specific Polymarket markets. We need to know exactly what market exists, its token IDs, interval, start/end, and rules.

Build:

- Gamma API client;
- recurring BTC Up/Down market discovery;
- parser for horizon/start/end;
- storage in `polymarket_markets`;
- official rules/resolution source capture;
- active/closed/resolved state updater;
- unit fixtures from real API responses.

Important:

- horizons are config-driven;
- do not assume 10m exists;
- rule changes must be detectable.

Acceptance:

- current 5m/15m BTC Up/Down markets can be found without manually pasting IDs.

---

## 2. Build the 24/7 recorder

Start recording immediately because private high-frequency history becomes more valuable every day.

### Polymarket collector

Record:

- market WebSocket events;
- book snapshots/changes;
- best bid/ask;
- spread;
- last trade;
- timestamps;
- reconnect/gap events.

### BTC collector

Begin with one primary venue, then add a secondary venue.

Record the minimum useful raw/aggregated data:

- trades;
- top/order-book data;
- best bid/ask;
- volume/flow;
- derivatives signals where available.

### Reliability

Implement:

- reconnect with backoff;
- health heartbeat;
- stale-feed detection;
- UTC timestamps;
- NTP requirement;
- idempotent writes;
- graceful restart.

Acceptance:

- 24-hour soak test;
- no unexplained data gaps;
- gaps/reconnects are explicitly logged.

---

## 3. Add retention and aggregation

Before raw data grows uncontrolled:

- decide snapshot/update retention;
- keep compact derived intervals;
- add database indexes/partitions as needed;
- measure disk growth/day;
- add disk-space alert.

Acceptance:

- estimate how long the free server can retain data;
- database stays performant.

---

## 4. Historical backfill

Build reproducible scripts for:

- Polymarket market/event history;
- Polymarket historical token prices;
- BTC candles/trades/other available history.

Save:

- source;
- download timestamp;
- source parameters;
- data version/checksum when practical.

Do not pretend unavailable historical order-book detail exists.

Acceptance:

- backfill can be re-run without duplicating/corrupting data;
- historical limitations are documented.

---

## 5. Official outcome/label pipeline

Create labels from official resolved markets.

For each target market:

- capture official outcome;
- capture start/end reference where available;
- verify market rule;
- attach label only after resolution.

Acceptance:

- labels are deterministic;
- unresolved markets cannot accidentally be treated as labels;
- leakage tests pass.

---

## 6. Feature engine

Build features in groups.

Start with:

1. Polymarket state;
2. BTC price/momentum;
3. basic trade flow;
4. order-book imbalance;
5. volatility;
6. time remaining/reference distance.

Then derivatives/cross-market features.

Rules:

- feature calculation only uses data available at feature timestamp;
- feature versions are immutable;
- missing data gets explicit flags.

Acceptance:

- same raw inputs + same feature version = same output;
- automated future-data test passes.

---

## 7. Baselines before fancy ML

Build:

- majority/naive baseline;
- simple market-price baseline;
- logistic regression;
- LightGBM or XGBoost.

Metrics:

- accuracy;
- balanced accuracy where useful;
- log loss;
- Brier score;
- calibration;
- confidence coverage;
- P&L under simulated execution.

Acceptance:

- boosted model must beat simple baselines out-of-sample before more complexity is justified.

---

## 8. Walk-forward backtester

Requirements:

- chronological data only;
- purging/embargo for overlap where required;
- no random leakage;
- configurable train/validation/test windows;
- frozen evaluation outputs;
- regime breakdown;
- realistic executable prices.

Acceptance:

- one command reproduces an evaluation report from a dataset/model version.

---

## 9. Probability calibration + edge engine

Implement calibrated probabilities.

Then compute potential trade edge using the executable market price.

First version may remain simple:

```text
gross_edge = p_model - executable_price
```

Then subtract/penalize for:

- spread;
- fees;
- slippage;
- uncertainty;
- staleness.

Acceptance:

- no trade when the configured minimum edge is not met;
- edge calculations have unit tests.

---

## 10. Live prediction engine

Run on live feeds with money disabled.

Every prediction stores:

- model version;
- feature version;
- timestamp;
- market;
- probability;
- predicted side;
- market bid/ask;
- edge;
- decision.

After resolution, append outcome/evaluation without rewriting the original prediction.

Acceptance:

- dashboard can prove predictions existed before outcomes.

---

## 11. Dashboard V1

Only build what is useful:

- active markets;
- model probabilities;
- market prices;
- edge/action;
- feed health;
- prediction history;
- accuracy/calibration;
- paper P&L;
- current mode.

Acceptance:

- someone can understand system health/performance without opening the database.

---

## 12. Paper execution

Implement the same interface live trading will use, but use simulated orders.

Model:

- bid/ask;
- depth;
- partial fills;
- latency;
- slippage;
- cancellations;
- expiry;
- fees.

Acceptance:

- paper trades reconcile against immutable signals.

---

## 13. Improvement loop

Test hypotheses, not random features.

For each experiment:

- hypothesis;
- data range;
- features;
- model;
- validation method;
- result;
- decision: reject/keep.

Use champion/challenger promotion.

Acceptance:

- no model is promoted because of one unusually good backtest.

---

## 14. Live readiness

Do not proceed unless Master Source of Truth live gate passes.

Add:

- Polymarket geoblock check;
- official SDK trading client;
- wallet/funder setup;
- secret storage;
- risk engine;
- order reconciliation;
- kill switch;
- live-mode interlock.

Acceptance:

- live mode defaults OFF;
- integration tests cannot accidentally spend money;
- explicit user authorization is documented before any real-money transition.

Engineering status: complete. Production non-spending host acceptance passed on exact candidate `5854e3003aa3340ce3733bf4532e204c1ec55836`, including SDK import, fail-closed interlock/risk checks, reconciliation, active-service checks, and `REAL_ORDER_SIDE_EFFECTS=0`. This does **not** mean the Master live gate passed.

---

## 15. Controlled live launch

Start deliberately small.

Measure:

- quoted vs filled price;
- latency;
- rejected/cancelled orders;
- slippage;
- live P&L;
- divergence from paper assumptions.

Do not automatically increase stake.

**Current status:** blocked. Do not begin Phase 15 until every Master Source of Truth live-gate item is `pass` and explicit real-money authorization exists.


A follow-up rollout/preflight environment-file checkpoint on `phase14-storage-rollout-preflight-env-file-binding` now preserves the independently verified preflight `ENV_FILE` as typed `env_file` JSON, requires it to exactly match rollout `PHASE14_PARTITIONED_STORAGE_ENV_FILE` before `gcloud config set project` or production VM contact, and records the configured path in eventual rollout PASS evidence. Clean tests-only RED head `f2b6ae64a0d60d33185622dfd0f705e9ef207a18` failed exactly the three missing contracts while 966 existing tests passed in CI `33961997010`; GREEN head `19ee0c82f6bdf2e402a3354006311e2cd11e87bb` passed CI `33962079609` with 969 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #100 merged rollout/preflight environment-file binding to `main` as `3e997104fb36bbcc342138bee3aa0a8dc5cb385d`. Final head `5fe989a5de89f034cca705d8a8edd9a9bdd4c208` passed push CI `33962257414`, PR CI `33962348039`, Historical Backfill Smoke `33962348023`, Live Recorder Smoke `33962348027`, and Recorder Short Soak `33962348024`; post-merge main CI `33962440603` then passed 969 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up rollout/preflight safety-binding checkpoint on `phase14-storage-rollout-preflight-safety-binding` now requires the verified-preflight `safety` block to preserve the exact research/zero-money boundary before `gcloud config set project` or production VM contact: `mode=research`, `live_trading_enabled=false`, zero trade/loss limits, and `automatic_promotion=false`. Clean tests-only RED head `927b8c34fbe37e72fb1cffa775b4dc5d6e35e085` failed exactly this missing contract while 969 existing tests passed in CI `33963087728`; GREEN head `8e74b75aac0615a421a163c4f8ba5118587f38f6` passed CI `33963162573` with 970 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #102 merged the rollout/preflight research/zero-money safety binding to `main` as `3a842b5b3c064dc782aeb83cde6eaf2a585f7f82`. Final head `2afd40d547d6b7c274ec660b59b0773cb22313b7` passed push CI `33963285443`, PR CI `33963371435`, Historical Backfill Smoke `33963371446`, Live Recorder Smoke `33963371430`, and Recorder Short Soak `33963371411`; post-merge main CI `33963460863` then passed 970 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up rollout/preflight critical-reserve checkpoint on `phase14-storage-rollout-preflight-critical-reserve-binding` now requires verified-preflight `headroom.critical_reserve_gib` to equal the unchanged 15 GiB reserve before `gcloud config set project` or production VM contact. Clean tests-only RED head `0476175546f72a83f80a08ec567b813f86e2bffa` failed exactly this missing contract while 970 existing tests passed in CI `33965037171`; GREEN head `f6b8367fd4d312d29117f46e569fd2a83fb2ea70` passed CI `33965126229` with 971 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #104 merged the rollout/preflight critical-reserve binding to `main` as `1289d1634c0ed358581ed8358b2302f782e99611`. Final head `f1ab47fb8b79d01054f72b03fa76276fe23f86d4` passed push CI `33965248512`, PR CI `33965349434`, Historical Backfill Smoke `33965349408`, Live Recorder Smoke `33965349384`, and Recorder Short Soak `33965349402`; post-merge main CI `33965440093` then passed 971 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up rollout local-evidence ordering checkpoint on `phase14-storage-rollout-local-evidence-before-gcloud` now completes verified-preflight file validation, single-snapshot JSON binding, and approved-preflight digest matching before any `gcloud` command, including the Cloud Shell authentication probe. Clean tests-only RED head `61d55549eb03f0bc0ff99ac49c928fde97adb390` failed exactly this missing ordering contract while 971 existing tests passed in CI `33966508887`; GREEN head `210833ea4d9278ab950c4f59911237394dc89aa2` passed CI `33966588761` with 972 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #106 merged the rollout local verified-preflight-before-gcloud ordering to `main` as `5c659fa133446417d8dc9b5f0d6d6db90bada2d0`. Final head `a2b21a6911a92b34ea708b3f23ddd9c9b23c0b34` passed push CI `33966736619`, PR CI `33966842331`, Historical Backfill Smoke `33966842222`, Live Recorder Smoke `33966842202`, and Recorder Short Soak `33966842313`; post-merge main CI `33966929138` then passed 972 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up verified-preflight JSON key-uniqueness checkpoint on `phase14-storage-rollout-preflight-json-duplicate-key-rejection` now rejects duplicate JSON object keys recursively inside the same single-read local evidence parser, before any `gcloud` command. Clean tests-only RED head `61e226bbd2210a9c635a6bad6b0cefb07365aabf` failed exactly the missing contract while 972 existing tests passed in CI `33967408925`. Implementation head `dab1ab2481ebf6e8bb405ad6abd48ed75520a41a` exposed only one stale same-snapshot test assertion; final repaired head `7e30444be91509e19629c98f8aa7e106d2dc40c1` passed CI `33967580571` with 973 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #108 merged recursive verified-preflight JSON duplicate-key rejection to `main` as `c1bcb3f6bba948f979509339c3995584e8ae48a7`. Final head `65b478b5a5823bc4fac343fb20fdf62c7e21c942` passed push CI `33967720522`, PR CI `33967811111`, Historical Backfill Smoke `33967811146`, Live Recorder Smoke `33967811084`, and Recorder Short Soak `33967811163`; post-merge main CI `33967910111` then passed 973 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up verified-preflight headroom-consistency checkpoint on `phase14-storage-rollout-preflight-headroom-consistency` now requires rollout to validate the reviewed preflight's integer `free_bytes`, `raw_total_bytes`, and `required_free_bytes` before any `gcloud` command, recompute `max(configured minimum, raw total bytes + 15 GiB)`, require exact equality with the verified requirement, and reject a PASS whose verified free bytes are below that requirement. Clean tests-only RED head `19071f8453ab05108325c25403a0c85c1a78c9ce` failed exactly this missing contract while 973 existing tests passed in CI `33968416654`; GREEN head `2fb70cda2a6892b7d07d66b990ad1ddfefd6f7af` passed CI `33968576546` with 974 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #110 merged verified-preflight headroom self-consistency validation to `main` as `c863d2545e3e0e02db06f3a885466ec03e15c009`. Final head `81e8de3da6b891112641b489df8138525be41d37` passed push CI `33968735460`, PR CI `33968822319`, Historical Backfill Smoke `33968822366`, Live Recorder Smoke `33968822411`, and Recorder Short Soak `33968822374`; post-merge main CI `33968910285` then passed 974 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up verified-preflight raw-shape consistency checkpoint on `phase14-storage-rollout-preflight-raw-shape-consistency` now requires the rollout, before any `gcloud` command, to reconcile top-level `storage_shape=legacy_unmigrated` with the verifier's underlying `raw` facts: `partitioned=false`, `legacy_table_present=false`, `dedupe_table_present=false`, and non-negative integer `estimated_rows`. Clean tests-only RED head `d24a8212c9c32d977d3e8de726d3ebb3584f2f46` failed exactly this missing contract while 974 existing tests passed in CI `33970455986`; GREEN head `20561a4fc86264314b6eac01a95690dea5e4d046` passed CI `33970547273` with 975 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #112 merged verified-preflight raw-shape consistency validation to `main` as `ad1d92b6bc618aa6c41728c434e4bc798b26f4f9`. Final head `05a3ce6f6e4f0c9e884b4471ec9cdea35edecf02` passed push CI `33970727415`, PR CI `33970835693`, Historical Backfill Smoke `33970835724`, Live Recorder Smoke `33970835743`, and Recorder Short Soak `33970835667`; post-merge main CI `33970937676` then passed 975 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up verified-preflight root-free consistency checkpoint on `phase14-storage-rollout-preflight-root-free-consistency` now requires rollout to validate the verifier's `headroom.root_free_bytes` as a positive integer before any `gcloud` command, matching the independent verifier's existing output contract. Clean tests-only RED head `7c245bb210c293f47a330154cf87dc31bf1f3c14` failed exactly this missing contract while 975 existing tests passed in CI `33971313534`; GREEN head `3c0c53236f4884247312eaf378ef3b6eb676c5aa` passed CI `33971328490` with 976 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.
---

## Immediate next action

**Run and independently verify the read-only Phase 14 production storage preflight when host access is available; keep the recorder stopped and preserve the separate explicit authorization gate for the actual partition migration.**

The partitioned-storage implementation, read-only preflight, deterministic transcript verifier, and operator-hardening wrapper are all merged to `main`. PR #52 merged the verifier layer as `b567d1118d93910a56462ea1b45d9f2a1f728f77`; PR #53 merged the one-command evidence operator path as `8c4f4eea533b01ada05307ef5e9fd4c5df304878`. Final PR-head gates on `be358f47de944e9d9318b8244fb732fb8b3202e1` passed push CI `33885085546`, PR CI `33885158191`, Historical Backfill Smoke `33885158365`, Live Recorder Smoke `33885158067`, and Recorder Short Soak `33885158081`; post-merge CI `33885404731` then passed 927 tests plus Ruff, deployment validation, health, and dashboard checks.

Run the evidence wrapper from a clean local checkout whose `HEAD` exactly equals `PHASE14_PARTITIONED_STORAGE_HEAD`. It captures the read-only preflight transcript in Cloud Shell and independently verifies it; the wrapper fails closed on a local candidate-head mismatch or dirty working tree. The underlying preflight now also reads `POSTGRES_USER` and `POSTGRES_DB` from the production environment with the existing `bp` defaults and clamps unknown PostgreSQL `reltuples` estimates to zero before emitting verifier input. Full operator steps are in `docs/PHASE-14-STORAGE-RECOVERY.md`.

The verifier still requires exact deployed/candidate SHA agreement, `RECORDER_STATE=stopped`, `MUTATIONS_PERFORMED=false`, canonical 24–48h recovery evidence, unequivocally unmigrated legacy raw storage, and sufficient migration headroom. Required free space is the larger of the configured floor and the current `raw_market_events` total relation size plus the unchanged 15 GiB critical reserve. Conflicting duplicate transcript fields fail closed.

A verified preflight PASS is prerequisite evidence only. The production partition migration remains a **separate explicit production authorization**. PR #54 merged the rollout-preflight parity hardening to `main` as `4e4ef004ba805438d8a7e68aa365d92754e57f4a`. The rollout now requires the verified preflight JSON itself, binds it to the exact deployed/candidate SHAs, records its SHA-256 in rollout evidence, and independently recomputes the dynamic migration-headroom rule before mutation and again immediately before migration apply. Final branch head `0601cf95d43bd949a7087645a3c4879d158e333d` passed push CI `33888063457`, PR CI `33888096498`, Historical Backfill Smoke `33888096374`, Live Recorder Smoke `33888096668`, and Recorder Short Soak `33888096363`; post-merge CI `33888318645` then passed 929 tests plus Ruff, deployment validation, health, and dashboard checks.

PR #56 merged the rollout live-schema-shape recheck to `main` as `fe025bc0ff204545fd4138aff9dd9c957572c248`. The production helper now independently re-queries the schema immediately before mutation and again after managed services are stopped; it fails closed if `raw_market_events` is already partitioned, `raw_market_events_legacy` already exists, or `raw_event_dedupe` already exists, preventing stale preflight evidence from authorizing a changed/partial schema. Final branch head `d56588ae0073eed5c350df9716a36f3a55a9a2df` passed push CI `33894032124`, PR CI `33894191483`, Historical Backfill Smoke `33894191580`, Live Recorder Smoke `33894191513`, and Recorder Short Soak `33894191530`; post-merge CI `33894382583` then passed 930 tests plus rollout syntax, health, and dashboard checks. This remains engineering evidence only and does not authorize production execution.

PR #57 merged machine enforcement for the separate production-migration approval gate to `main` as `987ee4d3029bc16977aebcda0e923bdc5d60ad0b`. The rollout requires explicit approved-from and approved-candidate SHA inputs that exactly equal the rollout transition; missing or mismatched approval fails before any production VM contact. Final branch head `ac1e94959f836bf019249a5e055e23685e339831` passed push CI `33895334052`, PR CI `33895337880`, Historical Backfill Smoke `33895337719`, Live Recorder Smoke `33895337810`, and Recorder Short Soak `33895337889`; post-merge CI `33895591223` then passed 931 tests plus rollout syntax, health, and dashboard checks. No approval values were set and production approval remains false.

PR #58 merged the physical-release acceptance proof to `main` as `c111ebdd29a584a05a46bfeb8d505b04f4558680`. After exact migration parity, the rollout must retire at least one non-empty verified raw partition, prove dedupe cleanup matches the archived row count, and show that total attached raw-partition relation bytes decrease across the maintenance cycle. Final branch head `604df58f48538a46518775053a5a638d54539122` passed push CI `33896515382`, PR CI `33896529731`, Historical Backfill Smoke `33896529562`, Live Recorder Smoke `33896529796`, and Recorder Short Soak `33896529803`; post-merge CI `33896713148` then passed 932 tests plus rollout syntax, health, and dashboard checks. This remains engineering evidence only; no production migration or approval occurred.

PR #59 merged the rollout local-candidate binding to `main` as `9ec294cc5f3aec29ece1c3628544d53a908c3b4b`. The Cloud Shell helper now resolves the local repository root, requires local `HEAD == PHASE14_PARTITIONED_STORAGE_HEAD`, and rejects any tracked or untracked working-tree change before any gcloud interaction. This prevents an older or locally modified rollout launcher from targeting a newer candidate whose safety gates differ. Final branch head `42549a77cdfcc6a9fcedd2ae2302ff24c525a299` passed push CI `33897575270`, PR CI `33897620395`, Historical Backfill Smoke `33897620428`, Live Recorder Smoke `33897620391`, and Recorder Short Soak `33897620444`; post-merge CI `33897830149` then passed 933 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #60 merged the rollout archive-evidence binding to `main` as `b4582d1c92026257c7c5e5b84fd87d1131432aa8`. The launcher extracts the verified preflight archive evidence name and `window_end`, passes both into the detached worker, and the worker requires that exact canonical recovery file and matching window instead of selecting the newest matching JSON. Final branch head `e37f58824afdd7de52a4373a77730056b4aa047c` passed push CI `33898669119`, PR CI `33898705601`, Historical Backfill Smoke `33898705613`, Live Recorder Smoke `33898705636`, and Recorder Short Soak `33898705599`; post-merge CI `33898942009` then passed 934 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #61 merged the archive-byte binding to `main` as `58455ac8ef9aa858a261fd48631f97866dffef38`. The read-only preflight now captures the canonical recovery evidence SHA-256, the verifier preserves that exact digest, and the rollout worker re-hashes the preflight-bound host file before mutation; changed bytes fail closed with `archive_evidence_digest_mismatch`. Final branch head `d3b9f20be85d55444d629a795ec5d21a89955c94` passed push CI `33901933926`, PR CI `33901981894`, Historical Backfill Smoke `33901981926`, Live Recorder Smoke `33901981957`, and Recorder Short Soak `33901982376`; post-merge CI `33902155063` then passed 937 tests plus preflight/rollout syntax, verifier compilation, health, and dashboard checks. No production execution or approval occurred.

PR #62 merged the immediate archive recheck to `main` as `1451e441dbed389a924285ec9396375e4d6f415c`. The worker repeats the complete preflight-bound archive name/SHA/window validation after managed services are stopped and the recorder is re-confirmed stopped, before candidate checkout/migration apply. Final branch head `47da8ccde36dba6f0e3e53a21a5653258f586b62` passed push CI `33902699410`, PR CI `33902733871`, Historical Backfill Smoke `33902733803`, Live Recorder Smoke `33902733733`, and Recorder Short Soak `33902733738`; post-merge CI `33902940041` then passed 938 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #63 merged rollout-evidence archive identity recording to `main` as `278834d91906b85fb26a9d4b850c968b7241e643`. Eventual rollout evidence now persists the exact preflight-bound recovery evidence SHA-256 and `window_end`, alongside the source archive path and verified-preflight SHA-256. Final branch head `44669e518326af15e44409881172d9274de749e4` passed push CI `33904091140`, PR CI `33904134033`, Historical Backfill Smoke `33904134048`, Live Recorder Smoke `33904134027`, and Recorder Short Soak `33904134087`; post-merge CI `33904327566` then passed 939 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #64 merged exact-state archive binding to `main` as `17475e26cf2b5dc520213e6d7b05591a4e43925e`. The clean candidate `PROJECT_STATE.json` now supplies the exact canonical recovery evidence path to the read-only remote preflight, replacing newest-by-mtime selection. Final branch head `3f214a17000bb6efceecd3d90c7d2caece454d91` passed push CI `33904722938`, PR CI `33904765174`, Historical Backfill Smoke `33904765122`, Live Recorder Smoke `33904765129`, and Recorder Short Soak `33904765163`; post-merge CI `33904960699` then passed 941 tests plus both preflight shell syntax checks, health, and dashboard checks. No production execution or approval occurred.

PR #65 merged preflight target-identity binding to `main` as `b0a9a0d57ed19fedf7f8492b3c1b31429d7f97fc`. Verified preflight JSON preserves `PROJECT`, `ZONE`, and `VM`, and the rollout rejects any mismatch before gcloud project selection or VM contact. Final branch head `aa1e5f68c2ebde309d335284f1afb4c79c3aa6c7` passed push CI `33905361519`, PR CI `33905405821`, Historical Backfill Smoke `33905405928`, Live Recorder Smoke `33905405816`, and Recorder Short Soak `33905405858`; post-merge main CI `33905610315` then passed 942 tests plus rollout syntax, verifier compilation, health, and dashboard checks. No production execution or approval occurred.

PR #66 merged rollout-evidence target identity to `main` as `16ccc92fb042ff2fa9885f93213c8f35495edc83`. Eventual rollout acceptance evidence now records the exact preflight-verified `PROJECT`, `ZONE`, and `VM`. Final branch head `bce2681e72d02f1c9ac29f31649224fca91e1c00` passed push CI `33906246243`, PR CI `33906289074`, Historical Backfill Smoke `33906288841`, Live Recorder Smoke `33906289071`, and Recorder Short Soak `33906289100`; post-merge main CI `33906470620` then passed 943 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #67 merged verified-preflight single-snapshot validation to `main` as `8629a076719416b0a81aee360e182cefe52c288d`. The rollout now reads verified-preflight bytes once and derives both the recorded SHA-256 and validated JSON fields from that same snapshot. Final branch head `029bdcb37887aedfd38200c6605e9717232d3f19` passed push CI `33906866448`, PR CI `33906908614`, Historical Backfill Smoke `33906908683`, Live Recorder Smoke `33906908581`, and Recorder Short Soak `33906908514`; post-merge main CI `33907100569` then passed 944 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #68 merged approval-to-preflight-digest binding to `main` as `44cdaece72fe4e9e7bc460c3e998aab21b54c1d0`. Explicit migration approval now includes the exact verified-preflight SHA-256 and fails closed on any mismatch before cloud project selection or VM contact. Final branch head `04152f58d88e775b048c7c7ba8c3d39a48a05c67` passed push CI `33907452114`, PR CI `33907500421`, Historical Backfill Smoke `33907500406`, Live Recorder Smoke `33907500442`, and Recorder Short Soak `33907500456`; post-merge main CI `33907692147` then passed 945 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #69 merged verified-preflight digest reporting to `main` as `8f582230aa9dccccea38263979444d27c6358835`. The read-only evidence wrapper now prints `VERIFIED_SHA256=<64-hex>` after deterministic verification so an approver can review the exact snapshot required by the separate approval gate, while explicitly never setting the approval variable itself. Tests-only RED head `cdc7018b0f57e2a7ade55168f92807187b40cee0` failed exactly the new digest-output contract while 945 existing tests passed in CI `33907751909`; GREEN/final head `0a479a981abab53187b31efb26f1bf3a2c070d2a` passed push CI `33907797066`, PR CI `33909028843`, Historical Backfill Smoke `33909028729`, Live Recorder Smoke `33909028693`, and Recorder Short Soak `33909028790`; post-merge main CI `33909248229` then passed 946 tests plus evidence-wrapper syntax, health, and dashboard checks. No production execution or approval occurred.

PR #70 merged explicit approval-scope evidence to `main` as `4665e1f2f44ce48b2d0bc816a1de07dea2d17272`. The detached worker rechecks the approved-from SHA, approved candidate SHA, and approved verified-preflight digest, and eventual PASS evidence records that exact authorization tuple. Final branch head `6738de3d0a8378a4341462b2e9f21473e5702d03` passed push CI `33909837433`, PR CI `33909887548`, Historical Backfill Smoke `33909887519`, Live Recorder Smoke `33909887558`, and Recorder Short Soak `33909887464`; post-merge main CI `33910096178` then passed 947 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #71 merged final rollback-material proof to `main` as `0910e86e70119145a880d47fce6ba9fa1e214928`. Final acceptance now requires `raw_market_events_legacy` to retain the exact original relation OID with nonzero bytes after maintenance and service restoration. Final branch head `85f0d419e8657c40700e953bd995fd6984b25e5e` passed push CI `33910606177`, PR CI `33910647686`, Historical Backfill Smoke `33910647693`, Live Recorder Smoke `33910647769`, and Recorder Short Soak `33910647694`; post-merge main CI `33910822989` then passed 948 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #72 merged the final post-restoration storage-health recheck to `main` as `b9e2a1c6905e781bbe332aa2676da8cde2264ad5`. The worker now reruns composite partitioned-storage health after managed-unit restoration and final rollback-material proof, and PASS evidence consumes that final snapshot. Final branch head `2e5993a1076a3016689fd3c6d13ed0c05c82286c` passed push CI `33913309701`, PR CI `33913349880`, Historical Backfill Smoke `33913349856`, Live Recorder Smoke `33913349841`, and Recorder Short Soak `33913349865`; post-merge main CI `33913522443` then passed 949 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #73 merged acceptance-evidence digest reporting to `main` as `926c0a37a792c76028a90ac66997e49a73198467`. The worker now hashes the installed final acceptance JSON and validates/prints `EVIDENCE_SHA256` before rollback is disarmed. Final branch head `77ab6d02714aa02ed557220c56ba13923d8ebb96` passed push CI `33913970548`, PR CI `33914019616`, Historical Backfill Smoke `33914019596`, Live Recorder Smoke `33914019574`, and Recorder Short Soak `33914019629`; post-merge main CI `33914224405` then passed 950 tests plus rollout syntax, health, and dashboard checks. No production execution or approval occurred.

PR #74 merged acceptance-evidence copy integrity to `main` as `f64c51a7703705c1bedde9a2b9f0ad489297ed92`. The worker hashes the generated temp JSON before install, hashes the installed canonical evidence after install, and fails closed with `rollout_evidence_copy_mismatch` unless the digests are identical before temp cleanup and rollback disarm. Clean tests-only RED head `c51c18d7ae3b3bc0241d563446a39c527b6e2ede` failed exactly the new byte-equality contract while 950 existing tests passed in CI `33914373039`; GREEN implementation head `02ccc1bee37fe3684cf2e9c045323aadf8681333` passed CI `33914527287` with 951 tests plus rollout syntax, health, and dashboard checks. Final branch head `3bd69fbb7e3a25ad8583cc305605ce8a01523e52` passed push CI `33914801853`, PR CI `33914857531`, Historical Backfill Smoke `33914857480`, Live Recorder Smoke `33914857492`, and Recorder Short Soak `33914857490`; post-merge main CI `33916124578` then passed 951 tests plus rollout syntax, health, and dashboard checks. No production preflight or migration was executed, no migration approval values were set, and the recorder remains stopped for storage recovery.

PR #76 merged acceptance-evidence durability to `main` as `7447d70f6b16c583e5f553d786c9649cd441cbda`. The worker flushes the filesystem containing the canonical installed acceptance JSON after generated/installed SHA-256 equality is proven and before temp cleanup or rollback disarm; a sync failure fails closed with `rollout_evidence_sync_failed` while rollback remains armed. Clean tests-only RED head `9196c96f626189431f5dde025c68954b4d6f8b89` failed exactly the new durability-ordering contract while 951 existing tests passed in CI `33917015980`; GREEN implementation head `5ac83363476dfe5aff7ab69d66094f5bcb8da299` passed CI `33917183016` with 952 tests plus rollout syntax, health, and dashboard checks. Final branch head `2cbeaa055c1e37a6268e7a26464fcc9dec9beeb9` passed push CI `33917470395`, PR CI `33917513711`, Historical Backfill Smoke `33917513493`, Live Recorder Smoke `33917513774`, and Recorder Short Soak `33917513615`; post-merge main CI `33917709776` then passed 952 tests plus rollout syntax, health, and dashboard checks. No production preflight or migration was executed, no migration approval values were set, and the recorder remains stopped for storage recovery.

PR #77 closed the acceptance-evidence durability integration record on `main` as `844f253d7a17afca305157937f7812189d0a9c41`. Final head `475646cd50d23d887268af76f598d54b8d05167d` passed push CI `33917946210`, PR CI `33917988880`, Historical Backfill Smoke `33917988837`, Live Recorder Smoke `33917988671`, and Recorder Short Soak `33917988802`; post-merge main CI `33919618388` then passed 952 tests plus rollout syntax, health, and dashboard checks. A follow-up rollback-evidence cleanup checkpoint on `phase14-storage-rollout-rollback-evidence-cleanup` now tracks when canonical PASS evidence has actually been installed. If any later failure occurs while rollback is still armed, the exit trap removes that unaccepted canonical artifact, syncs the evidence filesystem, logs cleanup PASS/FAILED, and then runs rollback regardless of cleanup outcome. Clean tests-only RED head `a96ce18e5db82ac764a11ce8208aaff26d7cd121` failed exactly the new cleanup-ordering contract while 952 existing tests passed in CI `33919826004`; GREEN implementation head `04bb8c779a1ed20f863207b2d83faa798bbf70dd` passed CI `33919998745` with 953 tests plus rollout syntax, health, and dashboard checks. This checkpoint is engineering-only until merged and does not authorize production execution.

PR #78 merged rollback-time acceptance-evidence cleanup to `main` as `d93825ebb39625dddba9af79ff4bf734e11d1866`. Final head `7faab47197d6e105af40176c08f8cab981992665` passed push CI `33920244547`, PR CI `33920273558`, Historical Backfill Smoke `33920273463`, Live Recorder Smoke `33920273488`, and Recorder Short Soak `33920273569`; post-merge main CI `33920435721` then passed 953 tests plus rollout syntax, health, and dashboard checks. PR #79 merged atomic acceptance-evidence publication to `main` as `f43fd5d85f1253c0d482526cea23d250d4b5801b`. The worker installs generated evidence to a hidden staging file on the evidence filesystem, syncs that staged file, then publishes it with a non-replacing hard link; an existing canonical pathname therefore causes publication failure instead of overwrite, and a failed stage copy never creates canonical PASS evidence. Clean tests-only RED head `7b6e222318498de14da53e300fd86eb4301732be` failed exactly the new exclusive-publication contract while 953 existing tests passed in CI `33920621979`. Initial implementation head `a90cf0685e3dc9f1c1a3e214041cd7d657454a2f` exposed three legacy ordering assertions still tied to direct install in CI `33920781374`; final repaired head `a0cc64aae7e78718f8a703fd3a92f76519fceed8` passed CI `33921020153` with 954 tests plus rollout syntax, health, and dashboard checks. Final branch head `5e0d0ed21259e56ff352579f6d0acef93d45eef9` passed push CI `33921253828`, PR CI `33921289164`, Historical Backfill Smoke `33921289187`, Live Recorder Smoke `33921288925`, and Recorder Short Soak `33921288949`; post-merge main CI `33921466538` then passed 954 tests plus rollout syntax, health, and dashboard checks. No production preflight or migration was executed, no migration approval values were set, and the recorder remains stopped for storage recovery.

PR #80 closed the atomic-publication integration record on `main` as `4054fc5fa1ab0de53cb3e55e66270e734a3d9dbd`. Final head `3de41e4012ed60150c9f5aad62ed3242e69eceba` passed push CI `33922467215`, PR CI `33922487128`, Historical Backfill Smoke `33922487129`, Live Recorder Smoke `33922487107`, and Recorder Short Soak `33922487103`; post-merge main CI `33922637255` then passed 954 tests plus rollout syntax, health, and dashboard checks. PR #81 merged Cloud Shell preflight-evidence no-clobber handling to `main` as `da439af9326d8e8c8cc5e6933ab75450e27dd0b9`. Transcript and verified-output paths must differ; each local evidence path is reserved with shell `noclobber`, transcript capture appends only after reservation, and a reserved verified JSON is removed if independent verification fails. Same-second reruns or explicit paths that already exist therefore fail closed instead of replacing earlier operator evidence. Clean tests-only RED head `839029eab35cc98336b5a12498660cb5aeec88b5` failed exactly the new local-evidence reservation contract while 954 existing tests passed in CI `33923655774`. Initial implementation head `85aee00a64e6acb04cd380a7062e4b0898ec5796` exposed one stale legacy `tee` assertion in CI `33923821630`; final repaired head `facd4a0c410ece29c32d901a9fe1eb20e2e0ac6d` passed CI `33923962021` with 955 tests plus rollout syntax, health, and dashboard checks. Final branch head `a8f5abd5017f11cc3aa907d4abac104473d86f16` passed push CI `33924157181`, PR CI `33924183647`, Historical Backfill Smoke `33924183595`, Live Recorder Smoke `33924183487`, and Recorder Short Soak `33924183667`; post-merge main CI `33924340156` then passed 955 tests plus rollout syntax, health, and dashboard checks. This integration remains operator-evidence hardening only: no production preflight or migration was executed and no migration authorization was set.

PR #82 closed the Cloud Shell evidence no-clobber integration record on `main` as `b35dd6b9746d67a03cd9a193eeaa181b9aa17494`. Final head `c5877ece0c87d55cd913d1c2e6150549e69486a4` passed push CI `33924526514`, PR CI `33924555154`, Historical Backfill Smoke `33924555107`, Live Recorder Smoke `33924555159`, and Recorder Short Soak `33924555164`; post-merge main CI `33924737056` then passed 955 tests plus rollout syntax, health, and dashboard checks. PR #83 merged verified preflight target binding to `main` as `2ac8fad310eddf767598ac0610e0d89c5cfdc96b`. The independent verifier CLI receives the expected production `PROJECT`, `ZONE`, and `VM` and rejects any mismatch before emitting verified JSON; the Cloud Shell operator wrapper passes the exact same target identity used by the host preflight. Clean tests-only RED head `f1538dddb91e90428743eeb10e11977eb02f4596` failed exactly the new target-binding contract while 955 existing tests passed in CI `33925213952`; GREEN head `0fe9d7b7695dcec106f354454b3c535158e34e8d` passed CI `33925474551` with 956 tests plus rollout syntax, health, and dashboard checks. Final branch head `829b1e5658fd409390f982f5901cf86b67e630c6` passed push CI `33925695975`, PR CI `33925723843`, Historical Backfill Smoke `33925723819`, Live Recorder Smoke `33925723818`, and Recorder Short Soak `33925723795`; post-merge main CI `33925903210` then passed 956 tests plus rollout syntax, health, and dashboard checks. No production preflight or migration was executed and no migration authorization was set.

PR #84 closed the verified-target-binding integration record on `main` as `97bbe426182cfacaace2b8aa64d929059d34f606`. Final head `c9506d161613dce47fb8a71bd4bb8a87bf5a87a5` passed push CI `33926105364`, PR CI `33926140785`, Historical Backfill Smoke `33926140869`, Live Recorder Smoke `33926140811`, and Recorder Short Soak `33926140830`; post-merge main CI `33926308381` then passed 956 tests plus rollout syntax, health, and dashboard checks. A follow-up verified-archive-binding checkpoint on `phase14-storage-preflight-verified-archive-binding` now requires the independent verifier CLI to receive the exact canonical recovery archive evidence path already selected by the Cloud Shell wrapper from `PROJECT_STATE.json`; any different archive path is rejected before verified JSON is emitted. Clean tests-only RED head `6480fa34d337c719a5a4139a12bd8be14021e715` failed exactly the new archive-binding contract while 956 existing tests passed in CI `33927179956`; GREEN head `1984f69b98e7ecd60cd30c35b976bd5462c5c8fb` passed CI `33927346608` with 957 tests plus rollout syntax, health, and dashboard checks. Final branch head `57d98a08eb81fcc2f3baa0a3eea540865f3ff807` passed push CI `33930495625`, PR CI `33930620522`, Historical Backfill Smoke `33930620498`, Live Recorder Smoke `33930620492`, and Recorder Short Soak `33930620510`. PR #88 merged the environment-file binding to `main` as `35b5b09d6e98067d438877ce24d0f5e8079a821d`; post-merge main CI `33930749893` then passed 959 tests plus rollout syntax, health, and dashboard checks. No production preflight or migration was executed and no migration authorization was set.

PR #85 merged exact recovery-archive binding to `main` as `84bd7935d8ea657f999a80c1c5cfd9fb12da7e85`. Final head `246e94afbf049c348ab1574748104d953205fbe2` passed push CI `33927542818`, PR CI `33927569436`, Historical Backfill Smoke `33927569464`, Live Recorder Smoke `33927569463`, and Recorder Short Soak `33927569484`; post-merge main CI `33927715393` then passed 957 tests plus rollout syntax, health, and dashboard checks. PR #86 merged configured free-space-threshold binding to `main` as `d24aff16c6ad4392ba797f869f6a03445cbc9c74`. The verifier reads `MIN_FREE_GIB` from the captured transcript and rejects any mismatch with the configured verifier threshold; the Cloud Shell wrapper explicitly forwards `PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB` to the verifier. Clean tests-only RED head `941d4670e181f5dbdea164b3081547190c67c211` failed exactly the new threshold-binding contract while 957 existing tests passed in CI `33927883440`; GREEN head `53c186b4378787e182859dbe4f15106dbd113b42` passed CI `33928075496` with 958 tests plus rollout syntax, health, and dashboard checks. Final branch head `bf307b4b17a359c238494697e804ea0ce0d0465f` passed push CI `33928281477`, PR CI `33928311555`, Historical Backfill Smoke `33928311601`, Live Recorder Smoke `33928311447`, and Recorder Short Soak `33928311436`; post-merge main CI `33928453785` then passed 958 tests plus rollout syntax, health, and dashboard checks. No production preflight or migration was executed and no migration authorization was set.

PR #87 closed the configured free-space-binding integration record on `main` as `52ff789c1caeb2476111efc5b2c4529794753976`. Final head `205bf38c3b23d988866cdca63c427524d3ab821f` passed push CI `33928759822`, PR CI `33929622818`, Historical Backfill Smoke `33929622815`, Live Recorder Smoke `33929622868`, and Recorder Short Soak `33929622873`; post-merge main CI `33929752391` then passed 958 tests plus rollout syntax, health, and dashboard checks. A follow-up environment-file-binding checkpoint on `phase14-storage-preflight-verified-env-file-binding` records `ENV_FILE` in the read-only preflight transcript and requires the independent verifier to match it to the exact environment-file path resolved by the Cloud Shell wrapper. The wrapper explicitly forwards that same path to both the preflight and verifier, so verified evidence cannot silently describe a different compose/database environment. Clean tests-only RED head `5fc84a23160bc118ebb927569407b9d027f54d23` failed exactly the new binding contract while 958 existing tests passed in CI `33929947172`. Initial implementation head `5307e7ffadd1e09ac1948a1430c29fa4c05679ed` exposed one stale CLI transcript fixture in CI `33930168330`; final repaired head `5364dccf884031c2ab3e3553616c34f9f46a538f` passed CI `33930285235` with 959 tests plus rollout syntax, health, and dashboard checks. This checkpoint is engineering-only until merged and does not execute the production preflight or authorize migration.

PR #89 closed the environment-file-binding integration record on `main` as `4fac6293ffd94eae2ea10f2e3a62e6282672afba`. Final head `fd6c73f706d9970f10e652342eb7d2342392ec06` passed push CI `33930921860`, PR CI `33931027053`, Historical Backfill Smoke `33931027058`, Live Recorder Smoke `33931027036`, and Recorder Short Soak `33931027038`; post-merge main CI `33933659341` then passed 959 tests plus rollout syntax, health, and dashboard checks. A follow-up verified research/zero-money checkpoint on `phase14-storage-preflight-verified-zero-money-binding` now records the exact already-checked `MODE`, `LIVE_TRADING_ENABLED`, `MAX_TRADE_SIZE_USD`, and `MAX_DAILY_LOSS_USD` values in the read-only host transcript. The independent verifier rejects anything other than `research`, `false`, `0`, and `0` respectively and records that safe boundary in verified JSON. Clean tests-only RED head `ae770eec5b0a5d63c4b03fb96df91a533806fd53` failed exactly the new verifier contract while 959 existing tests passed in CI `33933861573`; GREEN head `16a56064135f8e6a779a71e63ba9cb374e27898f` passed CI `33934002653` with 960 tests plus rollout syntax, health, and dashboard checks. This is engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #90 merged verified research/zero-money safety evidence to `main` as `0e1b6257ed9ea51d44314db9477045ceea63e42b`. Final head `16c2043caab88928509da6851cb0ebaf09c0460f` passed push CI `33934166276`, PR CI `33934188337`, Historical Backfill Smoke `33934188319`, Live Recorder Smoke `33934188412`, and Recorder Short Soak `33934188405`; post-merge main CI `33934316885` then passed 960 tests plus rollout syntax, health, and dashboard checks. A follow-up candidate-promotion checkpoint on `phase14-storage-preflight-verified-promotion-binding` now validates every `automatic_promotion` field in the exact local candidate `PROJECT_STATE.json` is false before any host preflight contact, writes `AUTOMATIC_PROMOTION=false` into the reserved transcript, and requires that value in the independent verifier and verified JSON. Clean tests-only RED head `d46c54f065fbbd76de11b8dfb0ac72cd6bde0e5d` failed exactly the new promotion-binding contract while 960 existing tests passed in CI `33934509086`; GREEN head `e253baca46e9ddd2e7906a978f6fe03f8cad8ec1` passed CI `33934639110` with 961 tests plus rollout syntax, health, and dashboard checks. This is engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #91 merged candidate automatic-promotion binding to `main` as `cdb94191d11cf66887a7edced01405be3df6e6a5`. Final head `77120f4409dffca7a58c0ad91ab270f8ca00d6c4` passed push CI `33934811990`, PR CI `33934846807`, Historical Backfill Smoke `33934846797`, Live Recorder Smoke `33934846810`, and Recorder Short Soak `33934846784`; post-merge main CI `33934991463` then passed 961 tests plus rollout/deployment validation, health, and dashboard checks. A follow-up verified-branch-binding checkpoint on `phase14-storage-preflight-verified-branch-binding` now validates and forwards the exact `PHASE14_PARTITIONED_STORAGE_BRANCH` through the evidence operator, records that selected remote branch in the read-only preflight transcript, and requires the independent verifier to match it before emitting JSON. Clean tests-only RED head `7c0e473469a8097b1d2a2d07aeb67bfdc9ea1dde` failed exactly the new branch-binding contract while 961 existing tests passed in CI `33935696092`. Initial implementation head `dbf757ebb27e7bf05df7bb2e6e5c9c4dce76ab57` exposed one stale transcript fixture in CI `33935842015`; final repaired head `f9c497a216669d20fd823147118eb089c6d37144` passed CI `33935922189` with 962 tests plus rollout/deployment validation, health, and dashboard checks. This checkpoint remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #92 merged verified remote-branch binding to `main` as `11a1aad961be3d0b372eacf8f85bb54682b791fe`. Final head `98d3383305d8d284b8f4629c7206281bdbb7fc7e` passed push CI `33936055750`, PR CI `33936135738`, Historical Backfill Smoke `33936135715`, Live Recorder Smoke `33936135718`, and Recorder Short Soak `33936135882`; post-merge main CI `33936241254` then passed 962 tests plus rollout/deployment validation, health, and dashboard checks. The binding is now recorded as `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up rollout/preflight branch-binding checkpoint on `phase14-storage-rollout-preflight-branch-binding` now requires the migration rollout to read `remote_branch` from the same verified-preflight byte snapshot used for candidate/target/archive validation and reject any mismatch with `PHASE14_PARTITIONED_STORAGE_BRANCH` before `gcloud config set project` or production VM contact. Clean tests-only RED head `686b0af808171d1402596486b3587033bdbf3a6a` failed exactly this missing contract while 962 existing tests passed in CI `33936867549`; GREEN head `728306b3742655cd8438bd511e7671aba1ceecba` passed CI `33936971906` with 963 tests plus rollout/deployment validation, health, and dashboard checks. This is engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #94 merged the rollout/preflight remote-branch binding to `main` as `b20e883041a469d6552796f84e9c905e44899bdf`. Final head `a9da161a7ecb73ef0f8865b60e632260c31facd9` passed push CI `33937121381`, PR CI `33937206772`, Historical Backfill Smoke `33937206857`, Live Recorder Smoke `33937207005`, and Recorder Short Soak `33937206836`; post-merge main CI `33937297658` then passed 963 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up rollout acceptance-evidence branch-identity checkpoint on `phase14-storage-rollout-evidence-branch-identity` now carries the already-validated worker `BRANCH` into the final PASS evidence and records it as `remote_branch`. Clean tests-only RED head `9e376f50f3a08a6c34ac941f4ca460650b147dbc` failed exactly this missing audit field while 963 existing tests passed in CI `33958228899`; GREEN head `9c824bdc3c822a57b4f7f5abd7e62959848a1be0` passed CI `33958306826` with 964 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #96 merged rollout acceptance-evidence branch identity to `main` as `0b71f4e6d5cf5e3794388428eb9daaee9628987e`. Final head `7e5baa7377e8bb616e0f42913834ff4b6f53f250` passed push CI `33958422803`, PR CI `33958518764`, Historical Backfill Smoke `33958518768`, Live Recorder Smoke `33958518788`, and Recorder Short Soak `33958518767`; post-merge main CI `33958630448` then passed 964 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

A follow-up rollout/preflight configured-floor checkpoint on `phase14-storage-rollout-preflight-min-free-binding` now requires the rollout's `PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB` to exactly match `headroom.minimum_free_gib` from the same verified-preflight byte snapshot before `gcloud config set project` or production VM contact. Eventual PASS evidence records the configured floor under `pre_migration_headroom.minimum_free_gib`. Clean tests-only RED head `8fbd49c96fd1d798754c8e7effb32cea38ddfa6c` failed exactly these two missing contracts while 964 existing tests passed in CI `33960005630`; GREEN head `c5b20aebed835fb6352c9e0669177c77591f103f` passed CI `33960086061` with 966 tests plus rollout/deployment validation, research-mode health, and dashboard checks. This remains engineering-only until merged; no production preflight or migration was executed and no migration authorization was set.

PR #98 merged the rollout/preflight configured-floor binding to `main` as `7ccc43aa4225787975612bcf23acebdd6ee2f3a8`. Final head `380d16fae89d5899f1016c62b0cd29f564982355` passed push CI `33960205917`, PR CI `33960306226`, Historical Backfill Smoke `33960306239`, Live Recorder Smoke `33960306219`, and Recorder Short Soak `33960306206`; post-merge main CI `33960402112` then passed 966 tests plus rollout/deployment validation, research-mode health, and dashboard checks. The checkpoint is now `MERGED_MAIN_NOT_PRODUCTION_RUN`; no production preflight or migration was executed and no migration authorization was set.

When the separate migration-authorization gate is eventually satisfied, the exact-SHA rollout helper must still retain rollback material, prove exact migration parity, run one verified archive-to-partition-drop cycle, prove physical relation-size release, and end with `RECORDER_RESTARTED=false`.

After storage migration/health acceptance, the next operational step is the already-merged recorder reliability repair with `RECORDER_WRITER_WORKERS=4`, followed by all-four-feed and natural-load no-drop/backpressure acceptance. Recorder restart remains separate from schema migration.

Existing V1 evidence remains a separate immutable epoch. Selected-book freshness remains exactly 10 seconds. `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false` remain mandatory. Gate B remains unauthorized, the Master live gate remains `fail`, geographic/compliance restrictions must not be bypassed, and Phase 15 remains blocked.