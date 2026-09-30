from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_approval import ApprovalError, validate_approved_handoff

FAST_LIVE_PURPOSE = "phase15-v3-fast-live-v1"
FAST_LIVE_WARMUP_PURPOSE = "phase15-v3-fast-live-warmup-v1"
FAST_LIVE_RESULT_PURPOSE = "phase15-v3-fast-live-result-v1"
FAST_LIVE_PREPARE_PURPOSE = "phase15-v3-fast-live-prepare-v1"
FAST_LIVE_APPROVAL_PURPOSE = "phase15-v3-fast-live-approval-v1"
FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE = "manual-telegram-continuous-v1"
FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE = "auto-telegram-continuous-v1"
FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2 = (
    "manual-telegram-continuous-v2"
)
FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2 = (
    "auto-telegram-continuous-v2"
)
FAST_LIVE_CONTINUOUS_AUTHORIZATION_MODE = (
    FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2
)
FAST_LIVE_CONTINUOUS_AUTHORIZATION_MODES = frozenset(
    {
        FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE,
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE,
        FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2,
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
    }
)
FAST_LIVE_CONTINUOUS_V1_AUTHORIZATION_MODES = frozenset(
    {
        FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE,
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE,
    }
)
FAST_LIVE_CONTINUOUS_V2_AUTHORIZATION_MODES = frozenset(
    {
        FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2,
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
    }
)
FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA = (
    "5676efcb60840f4533a7f43b3c6a7efab9e97541"
)
FAST_LIVE_PREPARE_MAX_AGE_SECONDS = Decimal("45")
FAST_LIVE_RESULT_MAX_AGE_SECONDS = Decimal("300")
FAST_LIVE_RESULT_STALL_SECONDS = Decimal("20")
FAST_LIVE_SOURCE_KEY = "fast_live_preauthorization"
FAST_LIVE_POLICY_VERSION = "v3-live-canary-v1"
FAST_LIVE_PREDICTION_VERSION = "v3-frozen-paper-v1"
FAST_LIVE_EXECUTION_VERSION = "paper-execution-v3-frozen-v1"
FAST_LIVE_TARGET_NOTIONAL_USD = Decimal("5")
FAST_LIVE_MAX_TRADE_SIZE_USD = Decimal("10")
FAST_LIVE_MAX_TOTAL_EXPOSURE_USD = Decimal("10")
FAST_LIVE_MAX_DAILY_LOSS_USD = Decimal("10")
FAST_LIVE_MAX_CONSECUTIVE_LOSSES = 1
FAST_LIVE_CONTINUOUS_V2_MAX_CONSECUTIVE_LOSSES = 0
FAST_LIVE_MIN_EDGE = Decimal("0.075")
FAST_LIVE_MAX_TRANSIT_SECONDS = Decimal("2")
FAST_LIVE_MIN_MARKET_END_SECONDS = Decimal("10")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class FastLiveError(RuntimeError):
    pass


class FastLiveRetryableError(FastLiveError):
    """Transient pre-attempt condition that may recover within the envelope lifetime."""

    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FastLiveError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FastLiveError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise FastLiveError(f"{name} must be finite")
    return result


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def payload_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def project_state_sha256(state: Mapping[str, Any]) -> str:
    return payload_sha256(state)


def request_sha256(prepared: Mapping[str, Any]) -> str:
    request = prepared.get("request")
    if not isinstance(request, Mapping):
        raise FastLiveError("prepared request missing")
    return payload_sha256(request)


def _source_authorization(state: Mapping[str, Any]) -> dict[str, Any]:
    phase = state.get("phase_15_v3_live_canary")
    if not isinstance(phase, Mapping):
        raise FastLiveError("phase 15 live source truth missing")
    authorization = phase.get(FAST_LIVE_SOURCE_KEY)
    if not isinstance(authorization, Mapping):
        raise FastLiveError("fast live source-truth authorization missing")
    return dict(authorization)


def verify_source_authorization(
    state: Mapping[str, Any],
    *,
    expected_main: str,
    observed_at: datetime,
    requires_telegram_approval: bool = False,
    continuous_session: bool = False,
    require_exact_main: bool = True,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if not _COMMIT_RE.fullmatch(expected_main):
        raise FastLiveError("expected main commit invalid")
    if continuous_session and not requires_telegram_approval:
        raise FastLiveError(
            "continuous fast live requires Telegram approval"
        )
    authorization = _source_authorization(state)
    authorization_mode = str(
        authorization.get("authorization_mode") or ""
    )
    if continuous_session:
        if authorization_mode in FAST_LIVE_CONTINUOUS_V1_AUTHORIZATION_MODES:
            expected_consecutive_losses = FAST_LIVE_MAX_CONSECUTIVE_LOSSES
        elif authorization_mode in FAST_LIVE_CONTINUOUS_V2_AUTHORIZATION_MODES:
            expected_consecutive_losses = (
                FAST_LIVE_CONTINUOUS_V2_MAX_CONSECUTIVE_LOSSES
            )
        else:
            raise FastLiveError(
                "fast live continuous authorization mode invalid"
            )
    else:
        expected_consecutive_losses = FAST_LIVE_MAX_CONSECUTIVE_LOSSES

    required = {
        "status": (
            "AUTHORIZED_CONTINUOUS_SESSION"
            if continuous_session
            else "AUTHORIZED_NOT_CONSUMED"
        ),
        "authorized": True,
        "target_notional_usd": 5,
        "max_trade_size_usd": 10,
        "max_total_exposure_usd": 10,
        "max_daily_loss_usd": 10,
        "max_consecutive_losses": expected_consecutive_losses,
        "min_edge": 0.075,
        "requires_telegram_approval": requires_telegram_approval,
        "prediction_version": FAST_LIVE_PREDICTION_VERSION,
        "execution_version": FAST_LIVE_EXECUTION_VERSION,
        "executor_country": "ZA",
    }
    if continuous_session:
        required.update(
            {
                "max_network_submission_attempts_per_intent": 1,
            }
        )
    else:
        required.update(
            {
                "consumed": False,
                "max_network_submission_attempts": 1,
            }
        )
    for name, expected in required.items():
        if authorization.get(name) != expected:
            raise FastLiveError(f"fast live source truth mismatch: {name}")
    if requires_telegram_approval:
        phase = state.get("phase_15_v3_live_canary")
        assert isinstance(phase, Mapping)
        auto = phase.get("operator_telegram_auto_approver")
        auto_active = isinstance(auto, Mapping) and (
            auto.get("live_auto_approve_authorized") is True
            and str(auto.get("status") or "").startswith("ACTIVE_")
        )
        if continuous_session:
            if authorization_mode not in (
                FAST_LIVE_CONTINUOUS_AUTHORIZATION_MODES
            ):
                raise FastLiveError(
                    "fast live continuous authorization mode invalid"
                )
            if (
                authorization_mode
                in {
                    FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE,
                    FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2,
                }
                and auto_active
            ):
                raise FastLiveError(
                    "fast live manual Telegram approval requires auto-approver disabled"
                )
            if (
                authorization_mode
                in {
                    FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE,
                    FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
                }
            ):
                if not auto_active:
                    raise FastLiveError(
                        "fast live auto Telegram approval requires auto-approver active"
                    )
                assert isinstance(auto, Mapping)
                if auto.get("approval_contract_git_blob_sha") != (
                    FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
                ):
                    raise FastLiveError(
                        "fast live auto Telegram approval contract mismatch"
                    )
                if auto.get("continuous_candidate_prompt_authorized") is not True:
                    raise FastLiveError(
                        "fast live continuous candidate auto-approval not authorized"
                    )
                if (
                    auto.get("continuous_fast_live_auto_approval_authorized")
                    is not True
                ):
                    raise FastLiveError(
                        "fast live continuous auto-approval not authorized"
                    )
                if authorization.get(
                    "auto_approval_contract_git_blob_sha"
                ) != FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA:
                    raise FastLiveError(
                        "fast live source auto-approval contract mismatch"
                    )
        elif auto_active:
            raise FastLiveError(
                "fast live manual Telegram approval requires auto-approver disabled"
            )
    authorization_id = str(authorization.get("authorization_id") or "")
    if not authorization_id or len(authorization_id) > 128:
        raise FastLiveError("fast live authorization id invalid")
    authorized_at_main = str(authorization.get("authorized_at_main") or "")
    if _COMMIT_RE.fullmatch(authorized_at_main) is None:
        raise FastLiveError("fast live authorization base commit invalid")
    if require_exact_main and authorized_at_main != expected_main:
        raise FastLiveError("fast live authorization base commit mismatch")
    max_transit = _decimal(
        authorization.get("max_transit_seconds"),
        "max_transit_seconds",
    )
    if max_transit != FAST_LIVE_MAX_TRANSIT_SECONDS:
        raise FastLiveError("fast live transit limit changed")
    expires = _utc(datetime.fromisoformat(str(authorization.get("expires_at") or "")))
    if observed >= expires:
        raise FastLiveError("fast live source-truth authorization expired")
    return authorization


def load_private_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise FastLiveError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise FastLiveError(f"{label} must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) not in {0o600, 0o640}:
        raise FastLiveError(f"{label} mode must be 0600 or 0640")
    if info.st_size <= 0 or info.st_size > 256 * 1024:
        raise FastLiveError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FastLiveError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise FastLiveError(f"{label} must contain an object")
    return dict(payload)


def verify_runtime_authorization(
    runtime: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    expected_main: str,
    observed_at: datetime,
    requires_telegram_approval: bool = False,
    continuous_session: bool = False,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    source = verify_source_authorization(
        state,
        expected_main=expected_main,
        observed_at=observed,
        requires_telegram_approval=requires_telegram_approval,
        continuous_session=continuous_session,
    )
    if runtime.get("schema_version") != 1:
        raise FastLiveError("runtime authorization schema invalid")
    if runtime.get("purpose") != FAST_LIVE_PURPOSE:
        raise FastLiveError("runtime authorization purpose invalid")
    if runtime.get("authorized") is not True:
        raise FastLiveError("runtime authorization not authorized")
    if str(runtime.get("authorization_id") or "") != str(source["authorization_id"]):
        raise FastLiveError("runtime authorization id mismatch")
    if str(runtime.get("release_main") or "") != expected_main:
        raise FastLiveError("runtime authorization release mismatch")
    if str(runtime.get("project_state_sha256") or "") != project_state_sha256(state):
        raise FastLiveError("runtime authorization source-truth hash mismatch")
    if bool(runtime.get("continuous_session", False)) != continuous_session:
        raise FastLiveError("runtime continuous session mode mismatch")
    if continuous_session:
        if runtime.get("authorization_mode") != source.get(
            "authorization_mode"
        ):
            raise FastLiveError(
                "runtime continuous authorization mode mismatch"
            )
        if runtime.get("authorization_mode") not in (
            FAST_LIVE_CONTINUOUS_AUTHORIZATION_MODES
        ):
            raise FastLiveError(
                "runtime continuous authorization mode invalid"
            )
        if runtime.get("max_network_submission_attempts_per_intent") != 1:
            raise FastLiveError(
                "runtime per-intent attempt limit changed"
            )
        if int(runtime.get("max_consecutive_losses", -1)) != int(
            source["max_consecutive_losses"]
        ):
            raise FastLiveError(
                "runtime consecutive-loss contract mismatch"
            )
    elif runtime.get("max_network_submission_attempts") != 1:
        raise FastLiveError("runtime authorization attempt limit changed")
    if bool(runtime.get("requires_telegram_approval", False)) != requires_telegram_approval:
        raise FastLiveError("runtime Telegram approval mode mismatch")
    if _decimal(runtime.get("target_notional_usd"), "target_notional_usd") != (
        FAST_LIVE_TARGET_NOTIONAL_USD
    ):
        raise FastLiveError("runtime authorization target changed")
    issued = _utc(datetime.fromisoformat(str(runtime.get("issued_at") or "")))
    expires = _utc(datetime.fromisoformat(str(runtime.get("expires_at") or "")))
    source_expires = _utc(datetime.fromisoformat(str(source["expires_at"])))
    if issued > observed or observed >= expires or expires > source_expires:
        raise FastLiveError("runtime authorization outside allowed window")
    return dict(runtime)


def validate_prepared(
    prepared: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if prepared.get("status") != "prepared":
        raise FastLiveError("candidate is not prepared")
    request = prepared.get("request")
    policy = prepared.get("policy")
    timing = prepared.get("timing")
    if not isinstance(request, Mapping):
        raise FastLiveError("prepared request missing")
    if not isinstance(policy, Mapping):
        raise FastLiveError("prepared policy missing")
    if not isinstance(timing, Mapping):
        raise FastLiveError("prepared timing missing")
    if policy.get("policy_version") != FAST_LIVE_POLICY_VERSION:
        raise FastLiveError("prepared policy version changed")
    policy_checks = {
        "max_trade_size_usd": FAST_LIVE_MAX_TRADE_SIZE_USD,
        "max_total_exposure_usd": FAST_LIVE_MAX_TOTAL_EXPOSURE_USD,
        "max_daily_loss_usd": FAST_LIVE_MAX_DAILY_LOSS_USD,
    }
    for name, expected in policy_checks.items():
        if _decimal(policy.get(name), name) != expected:
            raise FastLiveError(f"prepared policy changed: {name}")
    if int(policy.get("max_consecutive_losses", -1)) != (
        FAST_LIVE_MAX_CONSECUTIVE_LOSSES
    ):
        raise FastLiveError("prepared consecutive-loss limit changed")
    if int(policy.get("max_submission_attempts", 0)) != 1:
        raise FastLiveError("prepared attempt limit changed")
    if request.get("execution_version") != FAST_LIVE_EXECUTION_VERSION:
        raise FastLiveError("prepared execution version changed")
    if str(request.get("action") or "") != "BUY":
        raise FastLiveError("prepared action must be BUY")
    if str(request.get("selected_side") or "").lower() not in {"up", "down"}:
        raise FastLiveError("prepared selected side invalid")
    target = _decimal(request.get("target_notional_usd"), "target_notional_usd")
    price = _decimal(request.get("limit_price"), "limit_price")
    shares = _decimal(request.get("requested_shares"), "requested_shares")
    if target != FAST_LIVE_TARGET_NOTIONAL_USD:
        raise FastLiveError("prepared target changed")
    if not Decimal("0") < price <= Decimal("1"):
        raise FastLiveError("prepared limit price invalid")
    if shares <= 0 or price * shares > FAST_LIVE_MAX_TRADE_SIZE_USD:
        raise FastLiveError("prepared size exceeds live limit")
    for name in (
        "intent_id",
        "prediction_id",
        "paper_order_id",
        "request_id",
        "risk_decision_id",
    ):
        if not str(prepared.get(name) or ""):
            raise FastLiveError(f"prepared {name} missing")
    market_end = _utc(datetime.fromisoformat(str(prepared.get("market_end_at") or "")))
    remaining = Decimal(str((market_end - observed).total_seconds()))
    if remaining < FAST_LIVE_MIN_MARKET_END_SECONDS:
        raise FastLiveError("candidate too close to market end")
    prepared_at = _utc(
        datetime.fromisoformat(str(timing.get("prepared_observed_at") or ""))
    )
    if prepared_at > observed:
        raise FastLiveError("prepared timestamp is in the future")
    return {
        "intent_id": str(prepared["intent_id"]),
        "request_id": str(prepared["request_id"]),
        "risk_decision_id": str(prepared["risk_decision_id"]),
        "prediction_id": str(prepared["prediction_id"]),
        "paper_order_id": str(prepared["paper_order_id"]),
        "market_end_at": market_end.isoformat(),
        "prepared_at": prepared_at.isoformat(),
        "request_sha256": request_sha256(prepared),
        "prepared_sha256": payload_sha256(prepared),
        "request": dict(request),
    }


def _mac(body: Mapping[str, Any], key: bytes) -> str:
    if len(key) != 32:
        raise FastLiveError("fast live transport key must be 32 bytes")
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def create_envelope(
    prepared: Mapping[str, Any],
    *,
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    key_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    created = _utc(created_at)
    validated = validate_prepared(prepared, observed_at=created)
    if not key_id or len(key_id) > 64:
        raise FastLiveError("fast live key id invalid")
    auth_id = str(runtime_authorization.get("authorization_id") or "")
    auth_expires = _utc(
        datetime.fromisoformat(str(runtime_authorization.get("expires_at") or ""))
    )
    market_end = _utc(datetime.fromisoformat(validated["market_end_at"]))
    expires = min(
        created + timedelta(seconds=float(FAST_LIVE_MAX_TRANSIT_SECONDS)),
        market_end - timedelta(seconds=float(FAST_LIVE_MIN_MARKET_END_SECONDS)),
        auth_expires,
    )
    if expires <= created:
        raise FastLiveError("fast live envelope has no valid execution window")
    body = {
        "schema_version": 1,
        "purpose": FAST_LIVE_PURPOSE,
        "key_id": key_id,
        "authorization_id": auth_id,
        "intent_id": validated["intent_id"],
        "request_id": validated["request_id"],
        "risk_decision_id": validated["risk_decision_id"],
        "prediction_id": validated["prediction_id"],
        "paper_order_id": validated["paper_order_id"],
        "request_sha256": validated["request_sha256"],
        "prepared_sha256": validated["prepared_sha256"],
        "prepared": dict(prepared),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return {**body, "hmac_sha256": _mac(body, key)}


def verify_envelope(
    envelope: Mapping[str, Any],
    *,
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    supplied = str(envelope.get("hmac_sha256") or "")
    if not _SHA256_RE.fullmatch(supplied):
        raise FastLiveError("fast live envelope hmac invalid")
    body = {name: value for name, value in envelope.items() if name != "hmac_sha256"}
    if not hmac.compare_digest(supplied, _mac(body, key)):
        raise FastLiveError("fast live envelope hmac mismatch")
    if envelope.get("schema_version") != 1 or envelope.get("purpose") != FAST_LIVE_PURPOSE:
        raise FastLiveError("fast live envelope schema invalid")
    if str(envelope.get("key_id") or "") != expected_key_id:
        raise FastLiveError("fast live envelope key id mismatch")
    if str(envelope.get("authorization_id") or "") != str(
        runtime_authorization.get("authorization_id") or ""
    ):
        raise FastLiveError("fast live envelope authorization mismatch")
    created = _utc(datetime.fromisoformat(str(envelope.get("created_at") or "")))
    expires = _utc(datetime.fromisoformat(str(envelope.get("expires_at") or "")))
    if created > observed or observed >= expires:
        raise FastLiveError("fast live envelope expired or future")
    if Decimal(str((observed - created).total_seconds())) > FAST_LIVE_MAX_TRANSIT_SECONDS:
        raise FastLiveError("fast live envelope exceeded transit budget")
    prepared = envelope.get("prepared")
    if not isinstance(prepared, Mapping):
        raise FastLiveError("fast live prepared payload missing")
    validated = validate_prepared(prepared, observed_at=observed)
    for name in (
        "intent_id",
        "request_id",
        "risk_decision_id",
        "prediction_id",
        "paper_order_id",
        "request_sha256",
        "prepared_sha256",
    ):
        if str(envelope.get(name) or "") != str(validated[name]):
            raise FastLiveError(f"fast live envelope binding mismatch: {name}")
    return {
        **validated,
        "authorization_id": str(envelope["authorization_id"]),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }



def create_prepare_message(
    prepared: Mapping[str, Any],
    *,
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    key_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    created = _utc(created_at)
    validated = validate_prepared(prepared, observed_at=created)
    if not key_id or len(key_id) > 64:
        raise FastLiveError("fast live key id invalid")
    auth_expires = _utc(
        datetime.fromisoformat(str(runtime_authorization.get("expires_at") or ""))
    )
    market_end = _utc(datetime.fromisoformat(validated["market_end_at"]))
    expires = min(
        created + timedelta(seconds=float(FAST_LIVE_PREPARE_MAX_AGE_SECONDS)),
        market_end - timedelta(seconds=float(FAST_LIVE_MIN_MARKET_END_SECONDS)),
        auth_expires,
    )
    if expires <= created:
        raise FastLiveError("fast live prepare window closed")
    body = {
        "schema_version": 1,
        "purpose": FAST_LIVE_PREPARE_PURPOSE,
        "key_id": key_id,
        "authorization_id": str(runtime_authorization.get("authorization_id") or ""),
        "intent_id": validated["intent_id"],
        "request_id": validated["request_id"],
        "risk_decision_id": validated["risk_decision_id"],
        "prediction_id": validated["prediction_id"],
        "paper_order_id": validated["paper_order_id"],
        "request_sha256": validated["request_sha256"],
        "prepared_sha256": validated["prepared_sha256"],
        "prepared": dict(prepared),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return {**body, "hmac_sha256": _mac(body, key)}


def verify_prepare_message(
    payload: Mapping[str, Any],
    *,
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    supplied = str(payload.get("hmac_sha256") or "")
    if not _SHA256_RE.fullmatch(supplied):
        raise FastLiveError("fast live prepare hmac invalid")
    body = {name: value for name, value in payload.items() if name != "hmac_sha256"}
    if not hmac.compare_digest(supplied, _mac(body, key)):
        raise FastLiveError("fast live prepare hmac mismatch")
    if payload.get("schema_version") != 1 or payload.get("purpose") != FAST_LIVE_PREPARE_PURPOSE:
        raise FastLiveError("fast live prepare schema invalid")
    if str(payload.get("key_id") or "") != expected_key_id:
        raise FastLiveError("fast live prepare key id mismatch")
    if str(payload.get("authorization_id") or "") != str(
        runtime_authorization.get("authorization_id") or ""
    ):
        raise FastLiveError("fast live prepare authorization mismatch")
    created = _utc(datetime.fromisoformat(str(payload.get("created_at") or "")))
    expires = _utc(datetime.fromisoformat(str(payload.get("expires_at") or "")))
    if created > observed or observed >= expires:
        raise FastLiveError("fast live prepare expired or future")
    if Decimal(str((observed - created).total_seconds())) > FAST_LIVE_PREPARE_MAX_AGE_SECONDS:
        raise FastLiveError("fast live prepare exceeded age limit")
    prepared = payload.get("prepared")
    if not isinstance(prepared, Mapping):
        raise FastLiveError("fast live prepare payload missing")
    validated = validate_prepared(prepared, observed_at=observed)
    for name in (
        "intent_id",
        "request_id",
        "risk_decision_id",
        "prediction_id",
        "paper_order_id",
        "request_sha256",
        "prepared_sha256",
    ):
        if str(payload.get(name) or "") != str(validated[name]):
            raise FastLiveError(f"fast live prepare binding mismatch: {name}")
    return {
        **validated,
        "authorization_id": str(payload["authorization_id"]),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }


def create_approval_message(
    prepared: Mapping[str, Any],
    *,
    approval: Mapping[str, Any],
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    key_id: str,
    created_at: datetime,
    approval_prepared: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    created = _utc(created_at)
    approval_source = (
        prepared if approval_prepared is None else approval_prepared
    )
    try:
        approved = validate_approved_handoff(
            approval_source,
            approval=approval,
            observed_at=created,
        )
    except ApprovalError as exc:
        raise FastLiveError("fast live Telegram approval invalid") from exc
    validated = validate_prepared(prepared, observed_at=created)
    approval_validated = validate_prepared(
        approval_source,
        observed_at=created,
    )
    for name in ("prediction_id", "paper_order_id", "request_sha256"):
        if str(approval_validated[name]) != str(validated[name]):
            raise FastLiveError(
                f"fast live approved candidate mismatch: {name}"
            )
    auth_expires = _utc(
        datetime.fromisoformat(str(runtime_authorization.get("expires_at") or ""))
    )
    approval_expires = _utc(datetime.fromisoformat(str(approved["expires_at"])))
    market_end = _utc(datetime.fromisoformat(validated["market_end_at"]))
    expires = min(
        created + timedelta(seconds=float(FAST_LIVE_MAX_TRANSIT_SECONDS)),
        approval_expires,
        market_end - timedelta(seconds=float(FAST_LIVE_MIN_MARKET_END_SECONDS)),
        auth_expires,
    )
    if expires <= created:
        raise FastLiveError("fast live approval window closed")
    approval_binding = {
        "status": "approved",
        "intent_id": str(approved["intent_id"]),
        "prediction_id": validated["prediction_id"],
        "paper_order_id": validated["paper_order_id"],
        "request_sha256": validated["request_sha256"],
        "approved_at": approved["approved_at"],
        "expires_at": approved["expires_at"],
        "callback_query_id": approved["callback_query_id"],
    }
    body = {
        "schema_version": 1,
        "purpose": FAST_LIVE_APPROVAL_PURPOSE,
        "key_id": key_id,
        "authorization_id": str(runtime_authorization.get("authorization_id") or ""),
        "intent_id": validated["intent_id"],
        "request_sha256": validated["request_sha256"],
        "prepared_sha256": validated["prepared_sha256"],
        "prepare_sha256": approval_validated["prepared_sha256"],
        "approval_candidate_id": str(approved["intent_id"]),
        "prepared": dict(prepared),
        "approval_sha256": payload_sha256(approval_binding),
        "approval": approval_binding,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return {**body, "hmac_sha256": _mac(body, key)}


def verify_approval_message(
    payload: Mapping[str, Any],
    *,
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    supplied = str(payload.get("hmac_sha256") or "")
    if not _SHA256_RE.fullmatch(supplied):
        raise FastLiveError("fast live approval hmac invalid")
    body = {name: value for name, value in payload.items() if name != "hmac_sha256"}
    if not hmac.compare_digest(supplied, _mac(body, key)):
        raise FastLiveError("fast live approval hmac mismatch")
    if payload.get("schema_version") != 1 or payload.get("purpose") != FAST_LIVE_APPROVAL_PURPOSE:
        raise FastLiveError("fast live approval schema invalid")
    if str(payload.get("key_id") or "") != expected_key_id:
        raise FastLiveError("fast live approval key id mismatch")
    if str(payload.get("authorization_id") or "") != str(
        runtime_authorization.get("authorization_id") or ""
    ):
        raise FastLiveError("fast live approval authorization mismatch")
    created = _utc(datetime.fromisoformat(str(payload.get("created_at") or "")))
    expires = _utc(datetime.fromisoformat(str(payload.get("expires_at") or "")))
    if created > observed or observed >= expires:
        raise FastLiveError("fast live approval expired or future")
    prepared = payload.get("prepared")
    if not isinstance(prepared, Mapping):
        raise FastLiveError("fast live approval prepared payload missing")
    validated = validate_prepared(prepared, observed_at=observed)
    if str(payload.get("prepared_sha256") or "") != str(validated["prepared_sha256"]):
        raise FastLiveError("fast live approval prepared hash mismatch")
    if str(payload.get("intent_id") or "") != str(validated["intent_id"]):
        raise FastLiveError("fast live approval prepared intent mismatch")
    if str(payload.get("request_sha256") or "") != str(validated["request_sha256"]):
        raise FastLiveError("fast live approval prepared request mismatch")
    prepare_sha = str(payload.get("prepare_sha256") or "")
    if not _SHA256_RE.fullmatch(prepare_sha):
        raise FastLiveError("fast live prepare binding hash invalid")
    approval = payload.get("approval")
    if not isinstance(approval, Mapping) or approval.get("status") != "approved":
        raise FastLiveError("fast live approval payload invalid")
    approval_sha = str(payload.get("approval_sha256") or "")
    if approval_sha != payload_sha256(approval):
        raise FastLiveError("fast live approval binding hash mismatch")
    candidate_id = str(payload.get("approval_candidate_id") or "")
    if not candidate_id or candidate_id != str(approval.get("intent_id") or ""):
        raise FastLiveError("fast live approval candidate mismatch")
    for name in ("prediction_id", "paper_order_id", "request_sha256"):
        if str(approval.get(name) or "") != str(validated[name]):
            raise FastLiveError(f"fast live approval binding mismatch: {name}")
    approved_at = _utc(datetime.fromisoformat(str(approval.get("approved_at") or "")))
    approval_expires = _utc(datetime.fromisoformat(str(approval.get("expires_at") or "")))
    if approved_at > observed or observed >= approval_expires:
        raise FastLiveError("fast live human approval expired or future")
    return {
        "authorization_id": str(payload["authorization_id"]),
        "intent_id": str(payload["intent_id"]),
        "request_sha256": str(payload["request_sha256"]),
        "prepared_sha256": str(payload["prepared_sha256"]),
        "prepare_sha256": prepare_sha,
        "approval_candidate_id": candidate_id,
        "approval_sha256": approval_sha,
        "prepared": dict(prepared),
        "request": dict(validated["request"]),
        "prediction_id": str(validated["prediction_id"]),
        "paper_order_id": str(validated["paper_order_id"]),
        "approved_at": approved_at.isoformat(),
        "approval_expires_at": approval_expires.isoformat(),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }

def create_warmup_message(
    *,
    condition_id: str,
    token_ids: tuple[str, str],
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    key_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    created = _utc(created_at)
    condition = str(condition_id).strip()
    tokens = tuple(str(token).strip() for token in token_ids)
    if not condition or len(tokens) != 2 or not all(tokens) or tokens[0] == tokens[1]:
        raise FastLiveError("fast live warmup market identity invalid")
    auth_id = str(runtime_authorization.get("authorization_id") or "")
    auth_expires = _utc(
        datetime.fromisoformat(str(runtime_authorization.get("expires_at") or ""))
    )
    expires = min(created + timedelta(seconds=30), auth_expires)
    if expires <= created:
        raise FastLiveError("fast live warmup window closed")
    body = {
        "schema_version": 1,
        "purpose": FAST_LIVE_WARMUP_PURPOSE,
        "key_id": key_id,
        "authorization_id": auth_id,
        "condition_id": condition,
        "token_ids": list(tokens),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return {**body, "hmac_sha256": _mac(body, key)}


def verify_warmup_message(
    payload: Mapping[str, Any],
    *,
    runtime_authorization: Mapping[str, Any],
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    supplied = str(payload.get("hmac_sha256") or "")
    if not _SHA256_RE.fullmatch(supplied):
        raise FastLiveError("fast live warmup hmac invalid")
    body = {name: value for name, value in payload.items() if name != "hmac_sha256"}
    if not hmac.compare_digest(supplied, _mac(body, key)):
        raise FastLiveError("fast live warmup hmac mismatch")
    if payload.get("schema_version") != 1:
        raise FastLiveError("fast live warmup schema invalid")
    if payload.get("purpose") != FAST_LIVE_WARMUP_PURPOSE:
        raise FastLiveError("fast live warmup purpose invalid")
    if str(payload.get("key_id") or "") != expected_key_id:
        raise FastLiveError("fast live warmup key id mismatch")
    if str(payload.get("authorization_id") or "") != str(
        runtime_authorization.get("authorization_id") or ""
    ):
        raise FastLiveError("fast live warmup authorization mismatch")
    created = _utc(datetime.fromisoformat(str(payload.get("created_at") or "")))
    expires = _utc(datetime.fromisoformat(str(payload.get("expires_at") or "")))
    if created > observed or observed >= expires:
        raise FastLiveError("fast live warmup expired or future")
    condition = str(payload.get("condition_id") or "").strip()
    raw_tokens = payload.get("token_ids")
    if not condition or not isinstance(raw_tokens, list) or len(raw_tokens) != 2:
        raise FastLiveError("fast live warmup identity invalid")
    tokens = tuple(str(token).strip() for token in raw_tokens)
    if not all(tokens) or tokens[0] == tokens[1]:
        raise FastLiveError("fast live warmup token ids invalid")
    return {
        "condition_id": condition,
        "token_ids": tokens,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }


def marketable_depth(
    asks: list[tuple[object, object]] | tuple[tuple[object, object], ...],
    *,
    limit_price: object,
    requested_shares: object,
) -> dict[str, Any]:
    limit = _decimal(limit_price, "limit_price")
    requested = _decimal(requested_shares, "requested_shares")
    if requested <= 0 or not Decimal("0") < limit <= Decimal("1"):
        raise FastLiveError("marketability request invalid")
    best: Decimal | None = None
    depth = Decimal("0")
    for raw_price, raw_size in asks:
        price = _decimal(raw_price, "ask_price")
        size = _decimal(raw_size, "ask_size")
        if not Decimal("0") < price <= Decimal("1") or size <= 0:
            raise FastLiveError("order book ask invalid")
        if best is None or price < best:
            best = price
        if price <= limit:
            depth += size
    marketable = best is not None and best <= limit and depth > 0
    return {
        "marketable": marketable,
        "full_size_marketable": depth >= requested,
        "best_ask": None if best is None else format(best, "f"),
        "marketable_depth": format(depth, "f"),
        "requested_shares": format(requested, "f"),
        "limit_price": format(limit, "f"),
    }


def create_result_message(
    result: Mapping[str, Any],
    *,
    key: bytes,
    key_id: str,
    authorization_id: str,
    created_at: datetime,
) -> dict[str, Any]:
    created = _utc(created_at)
    intent_id = str(result.get("intent_id") or "")
    request_hash = str(result.get("request_sha256") or "")
    status = str(result.get("status") or "")
    if not intent_id or _SHA256_RE.fullmatch(request_hash) is None or not status:
        raise FastLiveError("fast live result binding invalid")
    body = {
        "schema_version": 1,
        "purpose": FAST_LIVE_RESULT_PURPOSE,
        "key_id": key_id,
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "request_sha256": request_hash,
        "status": status,
        "network_submission_attempt_consumed": (
            result.get("network_submission_attempt_consumed") is True
        ),
        "real_order_submitted": result.get("real_order_submitted") is True,
        "external_order_id": str(result.get("external_order_id") or ""),
        "result": dict(result),
        "created_at": created.isoformat(),
        "expires_at": (
            created
            + timedelta(seconds=float(FAST_LIVE_RESULT_MAX_AGE_SECONDS))
        ).isoformat(),
    }
    return {**body, "hmac_sha256": _mac(body, key)}


def verify_result_message(
    message: Mapping[str, Any],
    *,
    key: bytes,
    expected_key_id: str,
    expected_authorization_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    supplied = str(message.get("hmac_sha256") or "")
    if _SHA256_RE.fullmatch(supplied) is None:
        raise FastLiveError("fast live result hmac invalid")
    body = {name: value for name, value in message.items() if name != "hmac_sha256"}
    if not hmac.compare_digest(supplied, _mac(body, key)):
        raise FastLiveError("fast live result hmac mismatch")
    if (
        message.get("schema_version") != 1
        or message.get("purpose") != FAST_LIVE_RESULT_PURPOSE
    ):
        raise FastLiveError("fast live result schema invalid")
    if str(message.get("key_id") or "") != expected_key_id:
        raise FastLiveError("fast live result key id mismatch")
    if str(message.get("authorization_id") or "") != expected_authorization_id:
        raise FastLiveError("fast live result authorization mismatch")
    created = _utc(datetime.fromisoformat(str(message.get("created_at") or "")))
    expires = _utc(datetime.fromisoformat(str(message.get("expires_at") or "")))
    if created > observed or observed >= expires:
        raise FastLiveError("fast live result expired or future")
    result = message.get("result")
    if not isinstance(result, Mapping):
        raise FastLiveError("fast live result payload missing")
    for name in ("intent_id", "request_sha256", "status"):
        if str(message.get(name) or "") != str(result.get(name) or ""):
            raise FastLiveError(f"fast live result binding mismatch: {name}")
    if (message.get("network_submission_attempt_consumed") is True) != (
        result.get("network_submission_attempt_consumed") is True
    ):
        raise FastLiveError("fast live result attempt flag mismatch")
    if (message.get("real_order_submitted") is True) != (
        result.get("real_order_submitted") is True
    ):
        raise FastLiveError("fast live result order flag mismatch")
    return dict(result)


def load_transport_key(path: Path) -> bytes:
    payload = load_private_json(path, label="fast live transport key wrapper")
    encoded = str(payload.get("key_hex") or "")
    try:
        key = bytes.fromhex(encoded)
    except ValueError as exc:
        raise FastLiveError("fast live transport key invalid") from exc
    if len(key) != 32:
        raise FastLiveError("fast live transport key must be 32 bytes")
    return key
