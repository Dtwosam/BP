# Phase 14 Live Readiness V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed, auditable live-trading readiness boundary around the existing paper/live execution contract without activating or spending real money.

**Architecture:** Keep Phase 12's `ExecutionGateway` as the strategy-facing contract. Add a `bp_engine.live_readiness` subsystem for immutable readiness/risk/reconciliation evidence and a `PolymarketLiveExecutionGateway` that depends on a narrow local trading-client protocol; the production protocol adapter wraps the maintained official `polymarket-client` SDK only after all interlocks pass. Production remains RESEARCH/live-disabled/zero-limit throughout Phase 14 host acceptance.

**Tech Stack:** Python 3.12, Pydantic settings, SQLAlchemy/PostgreSQL 16, httpx, official `polymarket-client`, pytest, Ruff, GitHub Actions, Bash/systemd deployment assets.

**Spec:** `docs/superpowers/specs/2026-08-29-phase-14-live-readiness-v1-design.md`

## Global Constraints

- Production stays `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, `MAX_DAILY_LOSS_USD=0` during Phase 14.
- No test, CI job, host-acceptance script, CLI command, or dashboard endpoint may place/cancel/sign/fund/approve/settle a real-money order.
- No wallet private key, seed phrase, API secret, builder secret, or production credential is committed, logged, persisted, passed in argv, or requested in chat.
- Geographic eligibility uses `GET https://polymarket.com/api/geoblock`; blocked/error/invalid-schema responses fail closed. No proxy/VPN/restriction-bypass code.
- The official SDK target is the maintained `polymarket-client` package; the legacy `py-clob-client` is not used.
- A live spending path requires all interlocks together; no single environment variable can authorize it.
- Existing immutable predictions/paper ledgers/improvement evidence remain immutable.
- BUY-only V1; no martingale, loss chasing, automatic scale-up, or speculative early exit.
- Phase 15 remains blocked until the complete Master Source of Truth gate and explicit authorization pass.

---

### Task 1: SDK dependency compatibility and fail-closed settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/bp_engine/config.py`
- Create: `tests/live_readiness/test_config.py`

**Interfaces:**
- Produces `Settings` fields consumed by later tasks:
  - `max_total_exposure_usd: float = 0`
  - `max_consecutive_losses: int = 0`
  - `live_min_edge: float = 0`
  - `live_min_probability: float = 0`
  - `live_min_liquidity_usd: float = 0`
  - `live_max_spread: float = 0`
  - `live_max_prediction_age_seconds: float = 0`
  - `live_min_time_to_expiry_seconds: float = 0`
  - `live_cooldown_seconds: float = 0`
  - `live_activation_manifest_path: str = "/var/lib/bp/live/activation.json"`
  - `live_kill_switch_path: str = "/var/lib/bp/live/KILL"`
  - `polymarket_geoblock_url: str = "https://polymarket.com/api/geoblock"`
  - `polymarket_private_key_env: str = "POLYMARKET_PRIVATE_KEY"`
  - `polymarket_wallet_address_env: str = "POLYMARKET_WALLET_ADDRESS"`

- [ ] **Step 1: Write failing config tests**

```python
from bp_engine.config import Settings


def test_live_readiness_defaults_are_fail_closed() -> None:
    settings = Settings(_env_file=None)
    assert settings.live_trading_enabled is False
    assert settings.max_trade_size_usd == 0
    assert settings.max_daily_loss_usd == 0
    assert settings.max_total_exposure_usd == 0
    assert settings.max_consecutive_losses == 0
    assert settings.live_max_spread == 0
    assert settings.live_activation_manifest_path == "/var/lib/bp/live/activation.json"
    assert settings.live_kill_switch_path == "/var/lib/bp/live/KILL"


def test_live_readiness_env_overrides_parse_without_secrets(monkeypatch) -> None:
    monkeypatch.setenv("MAX_TOTAL_EXPOSURE_USD", "25")
    monkeypatch.setenv("LIVE_MAX_SPREAD", "0.04")
    settings = Settings(_env_file=None)
    assert settings.max_total_exposure_usd == 25
    assert settings.live_max_spread == 0.04
```

- [ ] **Step 2: Run focused test and require RED**

Run: `pytest tests/live_readiness/test_config.py -q`
Expected: FAIL because Phase 14 fields do not exist.

- [ ] **Step 3: Add settings and dependency compatibility**

Change BP's websocket requirement from `websockets>=16,<17` to `websockets>=15,<16` and add exact `polymarket-client==0.7.1`. Add the settings above with zero/fail-closed defaults. Do not add secret values to `Settings`; only add environment-variable *names* so the secrets loader controls last-moment access.

- [ ] **Step 4: Run tests and dependency install**

Run:
```bash
python -m pip install -e ".[dev]"
pytest tests/live_readiness/test_config.py -q
pytest tests/collectors -q
```
Expected: PASS and dependency resolution succeeds.

- [ ] **Step 5: Full CI gate**

Push commit `build: add phase14 sdk compatibility settings`; require Ruff, all pytest, deployment validation, health, dashboard test/typecheck/build GREEN before Task 2. PR smoke/short-soak later must also prove websocket compatibility.

---

### Task 2: Immutable live-readiness models and hashing

**Files:**
- Create: `src/bp_engine/live_readiness/__init__.py`
- Create: `src/bp_engine/live_readiness/models.py`
- Create: `src/bp_engine/live_readiness/hashing.py`
- Create: `tests/live_readiness/test_models_hashing.py`

**Interfaces:**
- Produce:
```python
LIVE_POLICY_VERSION = "live-risk-v1"
LIVE_READINESS_VERSION = "live-readiness-v1"

@dataclass(frozen=True)
class GeoblockResult:
    blocked: bool
    country: str
    region: str
    checked_at: datetime

@dataclass(frozen=True)
class ActivationManifest:
    authorized: bool
    git_sha: str
    authorization_id: str
    issued_at: datetime
    expires_at: datetime

@dataclass(frozen=True)
class LiveRiskPolicy:
    max_trade_size_usd: Decimal
    max_total_exposure_usd: Decimal
    max_daily_loss_usd: Decimal
    max_consecutive_losses: int
    min_edge: Decimal
    min_probability: Decimal
    min_liquidity_usd: Decimal
    max_spread: Decimal
    max_prediction_age_seconds: Decimal
    min_time_to_expiry_seconds: Decimal
    cooldown_seconds: Decimal
    policy_version: str = LIVE_POLICY_VERSION

@dataclass(frozen=True)
class LiveAccountSnapshot:
    total_exposure_usd: Decimal
    realized_daily_pnl_usd: Decimal
    consecutive_losses: int
    last_order_at: datetime | None
    unresolved_critical_reconciliation: int

@dataclass(frozen=True)
class LiveRiskContext:
    prediction_id: str
    prediction_semantic_sha256: str
    recorded_at: datetime
    market_end_at: datetime
    trade: bool
    executable: bool
    probability: Decimal
    expected_edge: Decimal
    selected_ask: Decimal | None
    spread: Decimal | None
    selected_liquidity_usd: Decimal | None
    requested_notional_usd: Decimal
    observed_at: datetime
    api_healthy: bool
    duplicate_intent: bool
    account: LiveAccountSnapshot

@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    reason: str

@dataclass(frozen=True)
class LiveRiskDecision:
    eligible: bool
    reasons: tuple[str, ...]
    rules: tuple[RuleResult, ...]
    policy_sha256: str
    semantic_sha256: str
```

- [ ] **Step 1: Write model validation/hash tests**

Cover timezone-aware timestamps, SHA-256 validation, probability/price ranges, nonnegative risk limits, deterministic canonical hashes, immutable tuples, and invalid policy limits.

- [ ] **Step 2: Run focused test and require RED**

Run: `pytest tests/live_readiness/test_models_hashing.py -q`
Expected: import failure.

- [ ] **Step 3: Implement canonical JSON/hash and models**

Use the project's existing canonical-hash semantics where practical; all dataclasses normalize UTC and `Decimal` deterministically. `LiveRiskPolicy` rejects negative values and rejects a positive per-trade limit greater than a positive total-exposure limit.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/live_readiness/test_models_hashing.py -q`
Expected: PASS.

- [ ] **Step 5: Commit and CI gate**

Commit `feat: add immutable live readiness models`; require GREEN.

---

### Task 3: Geoblock, activation manifest, kill switch, and secret metadata

**Files:**
- Create: `src/bp_engine/live_readiness/geoblock.py`
- Create: `src/bp_engine/live_readiness/interlock.py`
- Create: `src/bp_engine/live_readiness/secrets.py`
- Create: `tests/live_readiness/test_geoblock.py`
- Create: `tests/live_readiness/test_interlock.py`
- Create: `tests/live_readiness/test_secrets.py`

**Interfaces:**
- Produce:
```python
class GeoblockClient:
    def __init__(self, *, url: str, timeout_seconds: float = 5.0, client: httpx.Client | None = None): ...
    def check(self, *, observed_at: datetime | None = None) -> GeoblockResult: ...

class GeoblockError(RuntimeError): ...


def load_activation_manifest(path: str, *, expected_git_sha: str, observed_at: datetime) -> ActivationManifest: ...

def kill_switch_engaged(path: str) -> bool: ...

@dataclass(frozen=True)
class SecretMetadata:
    private_key_configured: bool
    wallet_configured: bool
    wallet_fingerprint: str | None


def secret_metadata(*, private_key_env: str, wallet_env: str, environ: Mapping[str, str] | None = None) -> SecretMetadata: ...

def load_private_key_for_sdk(*, private_key_env: str, environ: Mapping[str, str] | None = None) -> str: ...
```

- [ ] **Step 1: RED geoblock tests**

Use `httpx.MockTransport` to prove valid response parsing, `blocked=true`, invalid JSON/schema, non-2xx, and timeout/network exceptions. Invalid/error responses must raise `GeoblockError`; callers later convert this to a blocking reason.

- [ ] **Step 2: RED activation/kill-switch tests**

Use `tmp_path` to prove manifest SHA match, expiry, `authorized=true`, malformed JSON, missing file, and that kill-switch missing/unreadable/present states fail closed according to the spec.

- [ ] **Step 3: RED secret-boundary tests**

Ensure diagnostics never contain the private-key value. Wallet fingerprint must be derived only from the public wallet string. Missing secrets return configured=false metadata; `load_private_key_for_sdk` raises a generic configuration error without echoing the value.

- [ ] **Step 4: Implement minimal modules**

No proxy options, no region override, no country allow-list. `GeoblockClient` always checks the configured official endpoint directly.

- [ ] **Step 5: Run tests**

Run: `pytest tests/live_readiness/test_geoblock.py tests/live_readiness/test_interlock.py tests/live_readiness/test_secrets.py -q`
Expected: PASS.

- [ ] **Step 6: Commit and CI gate**

Commit `feat: add phase14 compliance and activation interlocks`; require GREEN.

---

### Task 4: Deterministic risk engine

**Files:**
- Create: `src/bp_engine/live_readiness/risk.py`
- Create: `tests/live_readiness/test_risk.py`

**Interfaces:**
- Produce:
```python
def evaluate_live_risk(
    *,
    policy: LiveRiskPolicy,
    context: LiveRiskContext,
    interlock_eligible: bool,
    interlock_reasons: tuple[str, ...] = (),
) -> LiveRiskDecision: ...
```

- [ ] **Step 1: Write one RED test per mandatory rule**

Tests cover exact machine-readable reasons:
- `live_interlock_blocked`
- `trade_signal_false`
- `prediction_not_executable`
- `trade_size_limit_exceeded`
- `total_exposure_limit_exceeded`
- `daily_loss_limit_reached`
- `consecutive_loss_limit_reached`
- `probability_below_minimum`
- `edge_below_minimum`
- `selected_ask_missing`
- `spread_missing`
- `spread_too_wide`
- `liquidity_missing`
- `liquidity_below_minimum`
- `prediction_stale`
- `too_close_to_expiry`
- `cooldown_active`
- `api_unhealthy`
- `duplicate_intent`
- `reconciliation_blocked`

Also test one fully eligible context and deterministic rule ordering/hash.

- [ ] **Step 2: Run focused tests and require RED**

Run: `pytest tests/live_readiness/test_risk.py -q`
Expected: import failure.

- [ ] **Step 3: Implement rule evaluation without short-circuiting**

Evaluate every rule so one decision contains the complete blocking reason set. Missing evidence is a failed rule. Daily loss uses `realized_daily_pnl_usd <= -max_daily_loss_usd` when the limit is positive. Cooldown compares `observed_at - last_order_at` with policy seconds.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/live_readiness/test_risk.py -q`
Expected: PASS.

- [ ] **Step 5: Commit and CI gate**

Commit `feat: add fail closed live risk engine`; require GREEN.

---

### Task 5: Append-only PostgreSQL live-readiness ledger

**Files:**
- Create: `src/bp_engine/storage/live_readiness_schema.py`
- Create: `migrations/0014_live_readiness.sql`
- Create: `src/bp_engine/live_readiness/repository.py`
- Modify: `src/bp_engine/storage/__init__.py`
- Create: `tests/live_readiness/test_repository_postgres.py`

**Interfaces:**
- Tables:
  - `live_readiness_checks`
  - `live_risk_decisions`
  - `live_order_intents`
  - `live_order_events`
  - `live_reconciliation_runs`
- Repository methods return `{created: bool, record: ...}`-style immutable results consistent with Phase 13 repositories.

- [ ] **Step 1: RED migration/schema tests**

Verify all tables, primary/natural keys, `semantic_sha256`, JSONB evidence columns, UTC timestamps, and append-only semantics.

- [ ] **Step 2: RED repository idempotency tests**

For each natural key: first identical insert creates, second identical insert returns existing, conflicting semantic payload raises a repository conflict error.

Required natural keys:
- readiness check: deterministic `check_id`;
- risk decision: deterministic `decision_id`;
- live intent: `(prediction_id, policy_version)` unique;
- event: deterministic `event_key` unique;
- reconciliation: deterministic `reconciliation_id`.

- [ ] **Step 3: Implement schema/migration/repository**

No UPDATE/DELETE repository methods for these evidence tables.

- [ ] **Step 4: Run PostgreSQL focused tests**

Run: `pytest tests/live_readiness/test_repository_postgres.py -q`
Expected: PASS against `BP_TEST_DATABASE_URL`.

- [ ] **Step 5: Commit and CI gate**

Commit `feat: add append only live readiness ledger`; require GREEN.

---

### Task 6: Official Polymarket SDK adapter behind a local protocol

**Files:**
- Create: `src/bp_engine/execution/live_client.py`
- Create: `tests/execution/test_live_client.py`

**Interfaces:**
- Produce local protocol:
```python
@dataclass(frozen=True)
class LiveClientOrderResult:
    accepted: bool
    external_order_id: str | None
    status: str
    code: str
    message: str

@dataclass(frozen=True)
class LiveClientCancelResult:
    cancelled: bool
    external_order_id: str
    status: str
    message: str

class LiveTradingClient(Protocol):
    def submit_limit_buy(self, *, token_id: str, price: Decimal, size: Decimal) -> LiveClientOrderResult: ...
    def cancel(self, *, external_order_id: str) -> LiveClientCancelResult: ...

class OfficialPolymarketTradingClient:
    @classmethod
    def create_from_environment(...): ...
```

- [ ] **Step 1: RED normalization tests using fake SDK objects**

Test accepted `AcceptedOrder`, rejected response, cancellation normalization, and exception normalization. Never call network.

- [ ] **Step 2: RED secret-construction test**

Monkeypatch the imported `polymarket.SecureClient.create` factory and prove it receives the secret only after `create_from_environment` is called, while object repr/errors never contain the secret.

- [ ] **Step 3: Implement adapter**

Use maintained imports from `polymarket`. For limit BUY, create the SDK order with exact token/price/size and `OrderSide.BUY`, then `post_order`. Normalize accepted/rejected responses immediately. Do not expose the SDK client outside this module.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/execution/test_live_client.py -q`
Expected: PASS with no network.

- [ ] **Step 5: Commit and CI gate**

Commit `feat: wrap official polymarket trading sdk`; require GREEN.

---

### Task 7: Live execution gateway with pre-submit intent and duplicate protection

**Files:**
- Create: `src/bp_engine/execution/live.py`
- Modify: `src/bp_engine/execution/__init__.py`
- Create: `tests/execution/test_live_gateway_postgres.py`

**Interfaces:**
- Produce `PolymarketLiveExecutionGateway` implementing existing `ExecutionGateway`.
- Constructor dependencies are injected:
```python
class PolymarketLiveExecutionGateway:
    def __init__(
        self,
        *,
        engine: Engine,
        repository: LiveReadinessRepository,
        policy: LiveRiskPolicy,
        client_factory: Callable[[], LiveTradingClient],
        interlock: Callable[[datetime], InterlockDecision],
        api_health: Callable[[], bool],
        now: Callable[[], datetime],
    ): ...
```

- [ ] **Step 1: RED tests prove default/blocked paths never call client factory**

Use a factory that raises `AssertionError("client factory must not be called")`. Cover research mode, live flag false, zero limits, missing/invalid activation, kill switch, blocked/error geoblock, duplicate intent, stale prediction, risk failure, and reconciliation blocker.

- [ ] **Step 2: RED synthetic eligible test**

With temporary explicit settings/interlock, a fake client, and a PostgreSQL fixture prediction, prove one eligible request creates one intent and one normalized accepted event. This is synthetic only and uses no real secret/network.

- [ ] **Step 3: RED idempotency/ambiguous submission tests**

An identical retry after an accepted/rejected/unknown event must not call submit again. If the fake client raises after intent creation, store `submission_unknown`; subsequent calls return a fail-closed ack until reconciliation resolves it.

- [ ] **Step 4: Implement gateway**

Always validate source prediction SHA and execution request identity before risk evaluation. Persist risk decision + intent before client construction. Map fail-closed decisions to `ExecutionOrderAck(accepted=False, reason=<machine_reason>)`.

- [ ] **Step 5: Cancellation tests and implementation**

Cancellation can proceed only for a known external order ID. Kill switch must not block risk-reducing cancellation. Unknown order IDs fail closed without calling the client.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/execution/test_live_gateway_postgres.py -q`
Expected: PASS.

- [ ] **Step 7: Commit and CI gate**

Commit `feat: add interlocked live execution gateway`; require GREEN.

---

### Task 8: Read-only reconciliation and readiness service

**Files:**
- Create: `src/bp_engine/live_readiness/service.py`
- Create: `tests/live_readiness/test_service_postgres.py`

**Interfaces:**
- Produce:
```python
class LiveReadinessService:
    def build_readiness_check(self, *, expected_git_sha: str, observed_at: datetime) -> ReadinessCheck: ...
    def store_readiness_check(...): ...
    def reconcile_snapshot(self, *, official_orders: Sequence[OfficialOrderSnapshot], observed_at: datetime) -> ReconciliationResult: ...
    def get_report(self) -> dict[str, object]: ...
```

- [ ] **Step 1: RED readiness report tests**

Prove production-default settings report `eligible=false` with live-disabled/zero-limit/activation/kill-switch reasons and never construct a secure client.

- [ ] **Step 2: RED reconciliation tests**

Cover intent without external result, external order without local intent, duplicate external IDs, overfill, price outside limit, cancellation disagreement, unknown/stale state, and clean synthetic reconciliation.

- [ ] **Step 3: Implement deterministic service**

Persist a semantic reconciliation run. `critical_discrepancy_count > 0` is an interlock blocker for new exposure.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/live_readiness/test_service_postgres.py -q`
Expected: PASS.

- [ ] **Step 5: Commit and CI gate**

Commit `feat: add live readiness and reconciliation service`; require GREEN.

---

### Task 9: Network-free/read-only CLI and dashboard diagnostics

**Files:**
- Create: `src/bp_engine/live_readiness/cli.py`
- Create: `src/bp_engine/live_readiness/__main__.py`
- Create: `scripts/run_live_readiness.py`
- Modify: `src/bp_engine/dashboard/service.py`
- Update dashboard types/components only if required by current snapshot contract.
- Create: `tests/live_readiness/test_cli.py`
- Modify/create dashboard tests for readiness fields.

**Interfaces:**
- CLI commands:
  - `report`
  - `validate-activation-manifest --path PATH --expected-head SHA`
  - `geoblock`
  - `reconcile-fixture --fixture PATH` for deterministic non-network validation only
- No `submit`, `buy`, `sell`, `place-order`, `cancel-live`, or `enable-live` CLI command.

- [ ] **Step 1: RED CLI tests**

Prove deterministic JSON, malformed manifest exit code, report from DB, and absence of spending commands in parser help.

- [ ] **Step 2: RED dashboard tests**

Add read-only fields:
```json
{
  "execution_available": false,
  "live_readiness": {
    "eligible": false,
    "authorized": false,
    "kill_switch_engaged": true,
    "geoblock_blocked": null,
    "country": null,
    "region": null,
    "wallet_configured": false,
    "reconciliation_status": "unavailable",
    "critical_discrepancy_count": null
  }
}
```
Default/missing evidence stays unavailable/false rather than optimistic zero.

- [ ] **Step 3: Implement CLI/dashboard read model**

No mutation endpoint or UI activation button.

- [ ] **Step 4: Run focused tests and dashboard checks**

Run:
```bash
pytest tests/live_readiness/test_cli.py tests/dashboard -q
cd dashboard && npm test && npm run typecheck && npm run build
```
Expected: PASS.

- [ ] **Step 5: Commit and CI gate**

Commit `feat: expose read only live readiness diagnostics`; require GREEN.

---

### Task 10: Deployment and exact-head Phase 14 host acceptance

**Files:**
- Create: `scripts/deploy/phase14_host_acceptance.sh`
- Create: `scripts/deploy/phase14_cloudshell_accept.sh`
- Create: `docs/PHASE-14-LIVE-READINESS.md`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/live_readiness/test_phase14_deployment_assets.py`

**Interfaces:**
- Host script accepts exact `EXPECTED_HEAD` and `BP_ENV_FILE`.
- Required PASS/INFO tokens:
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
  - final `PHASE14_HOST_ACCEPTANCE=PASS` only for the non-spending engineering acceptance, not the Master live gate.

- [ ] **Step 1: RED deployment-asset tests**

Assert scripts contain exact-head verification, service pre/post checks, direct geoblock read, no secret echo/argv extraction, zero-limit checks, fake-client/no-client submission guard, and final safety tokens.

- [ ] **Step 2: Implement scripts/runbook**

The host script must never load/print a private key and must not call `SecureClient.create`. It may import the SDK package/version and call only the public geoblock endpoint. The synthetic order path uses a fake client that would fail the script if invoked under production settings.

- [ ] **Step 3: CI syntax validation**

Add `bash -n` for both Phase 14 scripts and `python -m py_compile scripts/run_live_readiness.py`.

- [ ] **Step 4: Run full branch gates**

Require:
- Ruff GREEN;
- all Python tests GREEN;
- deployment validation GREEN;
- health remains research/live-disabled;
- dashboard tests/typecheck/build GREEN;
- Live Recorder Smoke GREEN;
- Recorder Short Soak GREEN;
- Historical Backfill Smoke GREEN.

- [ ] **Step 5: Run exact-head production host acceptance**

Use Cloud Shell helper against the exact all-green SHA. Record the real geoblock response. If blocked, preserve that as an explicit Master live-gate blocker; do not relocate or proxy traffic.

- [ ] **Step 6: Commit operational evidence only after real host output exists**

Commit sanitized host evidence with no IP/private key/secret. Do not fabricate missing tokens.

---

### Task 11: Master live-gate go/no-go closeout and Phase 15 boundary

**Files:**
- Create: `docs/evidence/phase-14-closeout-20260829.json` only from real evidence
- Modify: `PROJECT_STATE.json`
- Modify: `README.md`
- Modify: `START-HERE.md`
- Modify: `docs/BUILD-ORDER.md`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/DECISION-LOG.md` only if a new architectural/live-gate decision is made

**Interfaces:**
- Produce an explicit gate matrix for every Master Source of Truth item:
  - historical pipeline reproducible;
  - no known leakage;
  - time-ordered splits;
  - stable walk-forward evidence;
  - sufficiently large live paper sample with uncertainty;
  - positive after-cost profitability;
  - calibration acceptable;
  - risk/kill switch tested;
  - execution/reconciliation tested;
  - geographic/compliance eligible;
  - explicit user authorization.

- [ ] **Step 1: Build gate matrix from existing immutable evidence**

Every row must be `pass`, `fail`, or `insufficient_evidence`; never infer a pass from absence of data.

- [ ] **Step 2: Apply economic go/no-go**

Phase 13's first challenger tied the champion and lacked independent confirmation; this cannot be converted into a profitability proof. If live paper sample/after-cost evidence is insufficient or non-positive, mark the live gate closed even if engineering controls pass.

- [ ] **Step 3: Preserve geographic blocker if present**

If production geoblock returns blocked/error, mark geographic eligibility failed/unknown and keep Phase 15 blocked.

- [ ] **Step 4: Update project state truthfully**

Two valid outcomes:
- `PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED` with Phase 15 not permitted; or
- `PHASE_14_COMPLETE_PHASE_15_READY` only if every gate item except the final immediately recorded explicit activation authorization is genuinely satisfied and the user has explicitly authorized the transition.

Current user instruction to “go ahead” authorizes building Phase 14; it is not interpreted as permission to spend real money.

- [ ] **Step 5: Final exact-head CI and merge gate**

Run final branch CI/smokes on the closeout SHA, open PR, require fresh PR gates, merge with expected-head lock, and require final `main` CI GREEN before claiming Phase 14 engineering completion.
