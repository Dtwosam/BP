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

PR #80 closed the atomic-publication integration record on `main` as `4054fc5fa1ab0de53cb3e55e66270e734a3d9dbd`. Final head `3de41e4012ed60150c9f5aad62ed3242e69eceba` passed push CI `33922467215`, PR CI `33922487128`, Historical Backfill Smoke `33922487129`, Live Recorder Smoke `33922487107`, and Recorder Short Soak `33922487103`; post-merge main CI `33922637255` then passed 954 tests plus rollout syntax, health, and dashboard checks. A follow-up Cloud Shell evidence no-clobber checkpoint on `phase14-storage-preflight-local-evidence-no-clobber` now requires transcript and verified-output paths to differ, reserves each local evidence path with shell `noclobber`, appends the preflight transcript only after successful reservation, and removes a reserved verified JSON if independent verification fails. Same-second reruns or explicit paths that already exist therefore fail closed instead of replacing earlier operator evidence. Clean tests-only RED head `839029eab35cc98336b5a12498660cb5aeec88b5` failed exactly the new local-evidence reservation contract while 954 existing tests passed in CI `33923655774`. Initial implementation head `85aee00a64e6acb04cd380a7062e4b0898ec5796` exposed one stale legacy `tee` assertion in CI `33923821630`; final repaired head `facd4a0c410ece29c32d901a9fe1eb20e2e0ac6d` passed CI `33923962021` with 955 tests plus rollout syntax, health, and dashboard checks. This checkpoint changes only Cloud Shell evidence handling; it does not execute the production preflight or authorize migration.

When the separate migration-authorization gate is eventually satisfied, the exact-SHA rollout helper must still retain rollback material, prove exact migration parity, run one verified archive-to-partition-drop cycle, prove physical relation-size release, and end with `RECORDER_RESTARTED=false`.

After storage migration/health acceptance, the next operational step is the already-merged recorder reliability repair with `RECORDER_WRITER_WORKERS=4`, followed by all-four-feed and natural-load no-drop/backpressure acceptance. Recorder restart remains separate from schema migration.

Existing V1 evidence remains a separate immutable epoch. Selected-book freshness remains exactly 10 seconds. `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0`, and `automatic_promotion=false` remain mandatory. Gate B remains unauthorized, the Master live gate remains `fail`, geographic/compliance restrictions must not be bypassed, and Phase 15 remains blocked.
