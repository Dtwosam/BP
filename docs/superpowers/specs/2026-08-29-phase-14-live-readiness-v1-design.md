# Phase 14 Live Readiness V1 Design

## Goal

Build the complete safety and integration boundary required for a later controlled real-money launch while keeping the current system money-disabled. Phase 14 must prove that the code can fail closed around geography/compliance, wallet/signing, risk, order submission, cancellation, reconciliation, and emergency shutdown. It must not itself authorize or activate real trading.

## Non-negotiable boundaries

- Current production mode remains `RESEARCH`.
- `LIVE_TRADING_ENABLED=false` remains the production default and current value.
- `MAX_TRADE_SIZE_USD=0` and `MAX_DAILY_LOSS_USD=0` remain the production defaults and current values.
- Phase 14 may add live-order infrastructure, but host acceptance must not place, cancel, sign, fund, approve, or settle a real-money order.
- No private key, seed phrase, API secret, builder secret, wallet secret, or production credential may be committed, pasted into logs, placed in argv, or requested through chat.
- Live activation requires a later explicit authorization record plus all Master Source of Truth live-gate evidence. Passing Phase 14 engineering tests alone is not authorization.
- Geographic restrictions are fail-closed. The system must use Polymarket's current geoblock endpoint before any order submission and must not implement a proxy, VPN, alternate route, or other restriction bypass.
- The first live sizing policy remains conservative; no martingale, loss chasing, automatic scale-up, or adaptive stake increase is implemented here.
- Existing immutable predictions, paper orders, fills, settlements, and Phase 13 experiment evidence are never rewritten.

## External facts re-verified on 29 August 2026

Polymarket's current documentation says builders should check `GET https://polymarket.com/api/geoblock` before placing orders and that orders from blocked regions are rejected. The endpoint returns `blocked`, detected IP, country, and region. Current documentation lists the United States as close-only on both frontend and API; the Netherlands is listed as close-only on the frontend while the API itself is not restricted. These rules are external and changeable, so runtime eligibility must come from the endpoint rather than a hard-coded country list.

The legacy `Polymarket/py-clob-client` repository was archived in May 2026 and explicitly directs new integrations to the unified SDK. The maintained official Python repository is `Polymarket/py-sdk`, published as `polymarket-client`; the inspected package version is `0.7.1` and the public quickstart uses `polymarket.SecureClient`/`AsyncSecureClient` for authenticated trading.

A dependency constraint exists today: BP declares `websockets>=16,<17`, while `polymarket-client==0.7.1` declares `websockets>=13,<16`. BP's collector code uses a connector protocol rather than depending on a documented v16-only API, so Phase 14 will validate a shared `<16` websocket version under the complete recorder/CI suite before accepting the SDK dependency. If the compatibility suite fails, the SDK stays isolated behind an optional boundary rather than weakening the recorder.

## Approaches considered

### 1. Call the Polymarket SDK directly from prediction/paper services

This is the smallest amount of code but it mixes signing/network side effects into research logic, makes accidental live calls easier, and makes risk/reconciliation hard to test independently. Rejected.

### 2. Shared execution contract + fail-closed live gateway + official SDK adapter

Recommended. Keep the existing `ExecutionGateway` contract from Phase 12. Add a live gateway that depends on small protocols for geoblock, risk, kill-switch/activation state, and authenticated order transport. The official SDK is wrapped in the final adapter so tests can use deterministic fakes and cannot spend money. This preserves paper/live caller parity and creates auditable preflight and reconciliation boundaries.

### 3. Separate network execution microservice

This gives the strongest process isolation, but it adds deployment, IPC, service discovery, authentication, and operational complexity before the project has proven live economics. Defer until real live volume or security requirements justify it.

## Architecture

Phase 14 adds a `bp_engine.live_readiness` package and a live adapter beside the Phase 12 paper implementation.

1. `live_readiness.models` defines immutable risk policy, geoblock result, readiness decision, activation evidence, account/risk snapshot, order intent, and reconciliation result objects.
2. `live_readiness.geoblock` calls Polymarket's official geoblock endpoint with a bounded timeout, validates the response strictly, and fails closed on network/schema errors.
3. `live_readiness.interlock` evaluates the non-economic activation conditions: mode, live flag, nonzero explicit limits, activation manifest, kill switch, geoblock, signer/wallet configuration presence, and SDK health. It never returns eligible merely because one environment flag is true.
4. `live_readiness.risk` evaluates per-order and account-level controls from immutable prediction evidence plus current live ledger state.
5. `live_readiness.secrets` exposes only presence/identifier metadata to the rest of the app. The actual private key is read only by the final official-SDK factory and is never returned in diagnostics or persisted.
6. `execution.live` implements `ExecutionGateway` using the live-readiness preflight and a narrow `LiveTradingClient` protocol. The production implementation wraps the official `polymarket-client` SDK; tests use a fake client.
7. `live_readiness.repository` stores append-only live order intents/events, risk decisions, activation/readiness checks, and reconciliation runs. Requests are recorded before network submission so duplicate retries fail closed.
8. `live_readiness.service` performs read-only readiness reports and reconciliation. Phase 14 host acceptance exercises this service without creating a secure SDK client or submitting an order.
9. A network-free CLI prints deterministic JSON readiness/reconciliation reports and can validate a candidate activation manifest without activating trading.

## Live activation manifest

A single environment flag is insufficient to enter the spending path. V1 requires all of the following:

- `MODE=live`;
- `LIVE_TRADING_ENABLED=true`;
- `MAX_TRADE_SIZE_USD > 0`;
- `MAX_DAILY_LOSS_USD > 0`;
- a root/operator-managed activation manifest at a configured path;
- the manifest names the exact deployed Git SHA and an explicit authorization identifier;
- the manifest is valid JSON, not expired, and explicitly says `authorized=true`;
- kill switch is disengaged;
- current geoblock result is unblocked;
- wallet address and private-key environment variables are present;
- all risk configuration is internally valid and nonzero where required;
- reconciliation has no unresolved critical discrepancy;
- required live API health checks pass.

The repository never creates an authorized production manifest automatically. Phase 14 tests use temporary manifests and fake clients. The production host remains without a usable authorization manifest during Phase 14 acceptance.

## Kill switch

The kill switch is deliberately asymmetric and fail-closed.

- A configured filesystem path acts as the emergency stop.
- Missing/unreadable switch state is treated as engaged unless an explicit, valid activation state proves otherwise.
- An engaged switch blocks every new submit attempt before signer construction.
- Cancellation/reconciliation operations remain available when practical so exposure can be reduced, but no new exposure can be opened.
- Host acceptance proves the default/production state blocks order submission.

The switch path is outside the repository and does not contain secrets.

## Risk policy

Before any live BUY order, the risk engine must evaluate at least:

- global live interlock eligible;
- requested notional <= `max_trade_size_usd`;
- current total live exposure + requested notional <= `max_total_exposure_usd`;
- realized daily live loss has not reached `max_daily_loss_usd`;
- consecutive-loss threshold has not triggered a cooldown/stop;
- immutable prediction has `trade=true` and `executable=true`;
- probability/confidence meets configured minimum when configured;
- expected edge meets configured minimum;
- selected-side ask exists and is within `(0, 1]`;
- observed spread <= configured maximum;
- observed selected-side liquidity/depth >= configured minimum;
- prediction/market evidence is fresh enough;
- enough time remains before market expiry;
- no duplicate live order intent exists for the prediction/config;
- per-market/per-strategy cooldown has elapsed;
- API/data health is healthy;
- reconciliation has no unresolved critical discrepancy.

Every rule returns a machine-readable reason. Any missing evidence needed by a rule is rejection, not an assumed pass.

## Order semantics

Phase 14 reuses the Phase 12 `ExecutionOrderRequest` identity and limit-price semantics. V1 remains BUY-only and does not introduce speculative early exits.

For an eligible request:

1. Load and verify the immutable source prediction and semantic hash.
2. Build a risk context from the source prediction, current live exposure/loss state, recent order intents, health, geoblock, activation, and kill-switch state.
3. Persist an append-only risk decision and live order intent before any network call.
4. Only if the final decision is eligible, construct the official secure client at the last possible moment.
5. Use the official SDK to create a limit order for the exact token, side, size, and limit price.
6. Submit the signed order.
7. Persist the normalized accepted/rejected response without secrets.
8. Reconcile open orders/fills against the official account/order API.

An identical retry of an already-submitted natural key never sends a second order. Ambiguous network results are treated as `submission_unknown` and require reconciliation before any retry.

## Official SDK adapter

The adapter targets the maintained `polymarket-client` package. The production wrapper exposes only the operations needed by the execution contract, initially:

- create/sign a limit BUY order;
- post the signed order;
- cancel a specific order;
- fetch a specific/open order state needed for reconciliation;
- fetch account/balance information needed for safety checks when supported.

The rest of the code depends on a local protocol, not SDK concrete models. SDK response objects are normalized immediately into BP-owned immutable models.

Secret loading rules:

- `POLYMARKET_PRIVATE_KEY` and `POLYMARKET_WALLET_ADDRESS` are read from process environment/secret-manager injection only;
- no `.env` containing real secrets is committed;
- no secret appears in exception text, structured logs, database rows, command-line arguments, evidence files, or dashboard payloads;
- diagnostics expose only booleans such as `wallet_configured` and a redacted/checksummed public wallet identifier where necessary.

## Immutable storage

Phase 14 adds append-only ledgers rather than updating a mutable order row in place.

### `live_readiness_checks`

Stores candidate SHA, observed time, mode/flags/limit presence, activation-manifest fingerprint, kill-switch state, geoblock result, SDK/dependency health, reconciliation status, eligible flag, reasons, and semantic hash.

### `live_risk_decisions`

Stores source prediction identity/hash, policy version/hash, account/exposure snapshot, evidence inputs, rule outcomes, final eligible flag, reasons, and semantic hash.

### `live_order_intents`

One natural intent per `(prediction_id, live_policy_version)`. Stores request identity, exact size/limit, source risk-decision hash, pre-submit timestamp, and semantic hash. Creation occurs before external submission.

### `live_order_events`

Append-only normalized external events: accepted, rejected, submission_unknown, cancelled, observed_open, partially_filled, filled, expired, and reconciliation correction/evidence. Each row carries external order/trade identifiers when known and a semantic hash.

### `live_reconciliation_runs`

Stores read-only comparisons between BP intents/events and Polymarket account/order state, including unresolved/critical discrepancy counts and semantic hash.

Natural-key collisions with different semantic content fail closed.

## Reconciliation

Before live activation and continuously in Phase 15, reconciliation must detect:

- intent without known external result;
- external open order not represented by a BP intent;
- duplicate external order IDs;
- filled amount beyond requested amount;
- fill/price outside the original order contract;
- cancellation disagreement;
- stale/unknown external state;
- local exposure inconsistent with official positions/orders;
- any order created while a blocking risk rule or kill switch was active.

Critical unresolved discrepancies block all new exposure.

Phase 14 host acceptance uses only read-only/fake/no-credential reconciliation paths; it does not authenticate to a funded trading account.

## Geoblock and current host

The current production VM is in GCP `us-east1-c`. Current Polymarket documentation lists the United States as restricted for new API orders. Phase 14 therefore must not assume the existing VM is an eligible execution host. Host acceptance will call the official geoblock endpoint and record the real response. If `blocked=true`, Phase 14 records `geographic_eligibility_blocked` and the live gate remains closed.

No code in this phase automatically relocates traffic or uses a proxy/VPN to change the apparent jurisdiction. Any future execution-host placement must be separately reviewed for compliance and must follow Polymarket's published rules rather than evade them.

## Dependency compatibility

The official SDK dependency is introduced only after compatibility is proven.

Preferred dependency target:

- `polymarket-client==0.7.1` (or the exact current compatible release reverified at implementation time);
- shared websocket range compatible with both packages, expected `websockets>=15,<16` if BP tests prove it.

Acceptance requires the existing public recorder unit tests, live-recorder smoke workflow, and short-soak workflow to remain green. If they do not, the live SDK dependency is kept in an isolated optional environment/process and the Phase 14 design is amended rather than forcing an unsafe collector downgrade.

## CLI and operator reporting

Add a network-free/read-only command surface such as:

- `python -m bp_engine.live_readiness report` — summarize stored readiness/risk/reconciliation evidence;
- `... validate-activation-manifest --path ... --expected-head ...` — schema/fingerprint validation only;
- `... reconcile --dry-run` — no mutation/order side effect beyond storing a reconciliation evidence row when configured;
- `... geoblock` may perform only the official read-only geoblock request.

No CLI subcommand in Phase 14 is allowed to place a live order. Actual live submission remains an internal gateway capability reachable only after the complete interlock, and Phase 15 supplies the controlled live-run orchestration after explicit authorization.

## Dashboard contract

The dashboard may expose read-only live-readiness diagnostics:

- real execution available: false/true based on stored gate evidence;
- live activation authorized: false/true;
- kill switch engaged;
- geoblock eligibility and country/region without exposing IP if not needed;
- risk-policy status and limits;
- reconciliation status/critical discrepancy count;
- wallet configured boolean only;
- current mode and live flag.

It does not add a button that turns on LIVE mode, changes limits, uploads a private key, or places/cancels orders.

## Host acceptance

Phase 14 production-host acceptance is deliberately non-spending. It must prove on an exact CI-green SHA:

- existing recorder, PostgreSQL, dashboard API/web, and paper execution services stay active;
- `MODE=research` or otherwise non-live during acceptance;
- `LIVE_TRADING_ENABLED=false`;
- real-money limits remain zero;
- no production activation manifest authorizes live trading;
- kill switch/default interlock blocks submission;
- official geoblock check runs and its real response is recorded;
- SDK import/version/dependency compatibility is proven without constructing a secure/funded client;
- a synthetic eligible-looking order using a fake client is blocked by the production interlock before any client call;
- risk-engine tests cover every required rule;
- duplicate intent and ambiguous-submission reconciliation behavior are idempotent/fail-closed;
- read-only reconciliation reports zero internal ledger-integrity violations;
- dashboard reports `execution_available=false` unless the full gate, including explicit authorization, is genuinely satisfied;
- no real order side effect occurs.

If the host is geographically blocked, that is a valid Phase 14 readiness finding but not a pass of the Master Source of Truth live-trading gate. The project remains money-disabled and records the geographic blocker explicitly.

## Acceptance and phase boundary

Phase 14 engineering work is complete when:

- the official SDK adapter exists behind the shared gateway contract;
- dependency compatibility is proven in CI and recorder smoke/soak tests;
- geoblock, secrets boundary, activation manifest, kill switch, risk engine, live intent/event ledger, and reconciliation are implemented test-first;
- all live submission tests use fakes and prove default settings cannot spend money;
- production host acceptance records the real geoblock status and preserves all safety controls;
- no unresolved critical reconciliation/integrity defect exists;
- project documentation contains an explicit go/no-go report against every Master Source of Truth live-gate item.

Phase 15 Controlled Live Launch remains blocked unless the complete Master Source of Truth live gate passes, the current execution host/user setup is geographically/compliantly eligible, a dedicated limited-funds wallet is configured securely outside chat/source control, economic evidence is judged sufficient, and the user explicitly authorizes real-money activation. No automatic stake increase is ever implied by Phase 14 completion.
