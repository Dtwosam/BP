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

FAST_LIVE_PURPOSE = "phase15-v3-fast-live-v1"
FAST_LIVE_WARMUP_PURPOSE = "phase15-v3-fast-live-warmup-v1"
FAST_LIVE_SOURCE_KEY = "fast_live_preauthorization"
FAST_LIVE_POLICY_VERSION = "v3-live-canary-v1"
FAST_LIVE_PREDICTION_VERSION = "v3-frozen-paper-v1"
FAST_LIVE_EXECUTION_VERSION = "paper-execution-v3-frozen-v1"
FAST_LIVE_TARGET_NOTIONAL_USD = Decimal("5")
FAST_LIVE_MAX_TRADE_SIZE_USD = Decimal("10")
FAST_LIVE_MAX_TOTAL_EXPOSURE_USD = Decimal("10")
FAST_LIVE_MAX_DAILY_LOSS_USD = Decimal("10")
FAST_LIVE_MAX_CONSECUTIVE_LOSSES = 1
FAST_LIVE_MIN_EDGE = Decimal("0.075")
FAST_LIVE_MAX_TRANSIT_SECONDS = Decimal("2")
FAST_LIVE_MIN_MARKET_END_SECONDS = Decimal("10")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class FastLiveError(RuntimeError):
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
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if not _COMMIT_RE.fullmatch(expected_main):
        raise FastLiveError("expected main commit invalid")
    authorization = _source_authorization(state)
    required = {
        "status": "AUTHORIZED_NOT_CONSUMED",
        "authorized": True,
        "consumed": False,
        "target_notional_usd": 5,
        "max_trade_size_usd": 10,
        "max_total_exposure_usd": 10,
        "max_daily_loss_usd": 10,
        "max_consecutive_losses": 1,
        "min_edge": 0.075,
        "max_network_submission_attempts": 1,
        "requires_telegram_approval": False,
        "prediction_version": FAST_LIVE_PREDICTION_VERSION,
        "execution_version": FAST_LIVE_EXECUTION_VERSION,
        "executor_country": "ZA",
    }
    for name, expected in required.items():
        if authorization.get(name) != expected:
            raise FastLiveError(f"fast live source truth mismatch: {name}")
    authorization_id = str(authorization.get("authorization_id") or "")
    if not authorization_id or len(authorization_id) > 128:
        raise FastLiveError("fast live authorization id invalid")
    authorized_at_main = str(authorization.get("authorized_at_main") or "")
    if _COMMIT_RE.fullmatch(authorized_at_main) is None:
        raise FastLiveError("fast live authorization base commit invalid")
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
) -> dict[str, Any]:
    observed = _utc(observed_at)
    source = verify_source_authorization(
        state,
        expected_main=expected_main,
        observed_at=observed,
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
    if runtime.get("max_network_submission_attempts") != 1:
        raise FastLiveError("runtime authorization attempt limit changed")
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
