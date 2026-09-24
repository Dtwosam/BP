from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

POLICY_VERSION = "v3-live-canary-v1"
TARGET_NOTIONAL_USD = Decimal("5")
MIN_APPROVAL_WINDOW_SECONDS = 20
SUBMIT_SAFETY_FLOOR_SECONDS = 10
MAX_APPROVAL_LIFETIME_SECONDS = 45


class ApprovalError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ApprovalError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ApprovalError(f"{name} must be finite")
    return result


def request_sha256(prepared: Mapping[str, Any]) -> str:
    request = prepared.get("request")
    if not isinstance(request, Mapping):
        raise ApprovalError("prepared request missing")
    encoded = json.dumps(
        dict(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_prepared(
    prepared: Mapping[str, Any],
    *,
    observed_at: datetime,
    minimum_seconds_remaining: int = MIN_APPROVAL_WINDOW_SECONDS,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if prepared.get("status") != "prepared":
        raise ApprovalError("prepared status invalid")
    if prepared.get("action") != "submit":
        raise ApprovalError("prepared action invalid")

    policy = prepared.get("policy")
    request = prepared.get("request")
    if not isinstance(policy, Mapping) or not isinstance(request, Mapping):
        raise ApprovalError("prepared policy/request missing")
    if policy.get("policy_version") != POLICY_VERSION:
        raise ApprovalError("policy version mismatch")
    if int(policy.get("max_submission_attempts", 0)) != 1:
        raise ApprovalError("submission attempt policy mismatch")

    target = _decimal(request.get("target_notional_usd"), "target_notional_usd")
    if target != TARGET_NOTIONAL_USD:
        raise ApprovalError("target notional changed")

    intent_id = str(prepared.get("intent_id") or "")
    prediction_id = str(prepared.get("prediction_id") or "")
    paper_order_id = str(prepared.get("paper_order_id") or "")
    if not intent_id or not prediction_id or not paper_order_id:
        raise ApprovalError("prepared identity missing")

    market_end_raw = str(prepared.get("market_end_at") or "")
    try:
        market_end = datetime.fromisoformat(market_end_raw)
    except ValueError as exc:
        raise ApprovalError("market_end_at invalid") from exc
    market_end = _utc(market_end)
    seconds_remaining = (market_end - observed).total_seconds()
    if seconds_remaining < minimum_seconds_remaining:
        raise ApprovalError("prepared intent too close to market end")

    selected_side = str(request.get("selected_side") or prepared.get("selected_side") or "")
    limit_price = _decimal(request.get("limit_price"), "limit_price")
    requested_shares = _decimal(request.get("requested_shares"), "requested_shares")
    if selected_side not in {"up", "down"}:
        raise ApprovalError("selected side invalid")
    if not Decimal("0") < limit_price <= Decimal("1"):
        raise ApprovalError("limit price invalid")
    if requested_shares <= 0:
        raise ApprovalError("requested shares invalid")

    return {
        "intent_id": intent_id,
        "prediction_id": prediction_id,
        "paper_order_id": paper_order_id,
        "market_end_at": market_end.isoformat(),
        "seconds_remaining": seconds_remaining,
        "selected_side": selected_side,
        "limit_price": format(limit_price, "f"),
        "requested_shares": format(requested_shares, "f"),
        "target_notional_usd": format(target, "f"),
        "request_sha256": request_sha256(prepared),
    }


def build_prompt(prepared: Mapping[str, Any], *, observed_at: datetime) -> str:
    validated = validate_prepared(prepared, observed_at=observed_at)
    side = validated["selected_side"].upper()
    remaining = float(validated["seconds_remaining"])
    return (
        "BP V3 LIVE TRADE READY\n\n"
        f"Side: {side}\n"
        f"Limit: {validated['limit_price']}\n"
        f"Shares: {validated['requested_shares']}\n"
        f"Maximum spend: ${validated['target_notional_usd']}\n"
        f"Time remaining: {remaining:.1f}s\n\n"
        "Approve only if you want this exact real-money order submitted."
    )


def new_pending(
    prepared: Mapping[str, Any],
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    created_at: datetime,
    nonce: str | None = None,
) -> dict[str, Any]:
    validated = validate_prepared(prepared, observed_at=created_at)
    created = _utc(created_at)
    market_end = datetime.fromisoformat(validated["market_end_at"]).astimezone(UTC)
    expires = min(
        created + timedelta(seconds=MAX_APPROVAL_LIFETIME_SECONDS),
        market_end - timedelta(seconds=SUBMIT_SAFETY_FLOOR_SECONDS),
    )
    if expires <= created:
        raise ApprovalError("approval window already closed")
    if nonce is None:
        nonce = secrets.token_urlsafe(12)
    if not nonce or ":" in nonce or len(nonce.encode("utf-8")) > 40:
        raise ApprovalError("approval nonce invalid")

    return {
        "schema_version": 1,
        "status": "pending",
        "intent_id": validated["intent_id"],
        "prediction_id": validated["prediction_id"],
        "paper_order_id": validated["paper_order_id"],
        "request_sha256": validated["request_sha256"],
        "telegram_user_id": int(telegram_user_id),
        "telegram_chat_id": int(telegram_chat_id),
        "nonce": nonce,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
    }


def callback_data(action: str, nonce: str) -> str:
    normalized = action.lower()
    if normalized not in {"approve", "skip"}:
        raise ApprovalError("callback action invalid")
    value = f"{normalized}:{nonce}"
    if len(value.encode("utf-8")) > 64:
        raise ApprovalError("callback data exceeds Telegram limit")
    return value


def validate_callback(
    update: Mapping[str, Any],
    *,
    pending: Mapping[str, Any],
    observed_at: datetime,
) -> str:
    observed = _utc(observed_at)
    if pending.get("status") != "pending":
        raise ApprovalError("approval is not pending")
    expires = datetime.fromisoformat(str(pending["expires_at"])).astimezone(UTC)
    if observed >= expires:
        raise ApprovalError("approval expired")

    callback = update.get("callback_query")
    if not isinstance(callback, Mapping):
        raise ApprovalError("callback query missing")
    user = callback.get("from")
    message = callback.get("message")
    if not isinstance(user, Mapping) or not isinstance(message, Mapping):
        raise ApprovalError("callback identity missing")
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        raise ApprovalError("callback chat missing")
    if str(chat.get("type") or "") != "private":
        raise ApprovalError("approval must come from private chat")
    if int(user.get("id", 0)) != int(pending["telegram_user_id"]):
        raise ApprovalError("telegram user mismatch")
    if int(chat.get("id", 0)) != int(pending["telegram_chat_id"]):
        raise ApprovalError("telegram chat mismatch")

    data = str(callback.get("data") or "")
    approve = callback_data("approve", str(pending["nonce"]))
    skip = callback_data("skip", str(pending["nonce"]))
    if data == approve:
        return "approve"
    if data == skip:
        return "skip"
    raise ApprovalError("callback nonce/action mismatch")


def approval_record(
    *,
    action: str,
    pending: Mapping[str, Any],
    callback_query_id: str,
    approved_at: datetime,
) -> dict[str, Any]:
    normalized = action.lower()
    if normalized not in {"approve", "skip"}:
        raise ApprovalError("approval action invalid")
    approved = _utc(approved_at)
    expires = datetime.fromisoformat(str(pending["expires_at"])).astimezone(UTC)
    if approved >= expires:
        raise ApprovalError("approval expired")
    return {
        "schema_version": 1,
        "status": "approved" if normalized == "approve" else "skipped",
        "intent_id": str(pending["intent_id"]),
        "prediction_id": str(pending["prediction_id"]),
        "paper_order_id": str(pending["paper_order_id"]),
        "request_sha256": str(pending["request_sha256"]),
        "telegram_user_id": int(pending["telegram_user_id"]),
        "telegram_chat_id": int(pending["telegram_chat_id"]),
        "callback_query_id": str(callback_query_id),
        "approved_at": approved.isoformat(),
        "expires_at": str(pending["expires_at"]),
    }

def validate_approved_handoff(
    prepared: Mapping[str, Any],
    *,
    approval: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if approval.get("status") != "approved":
        raise ApprovalError("approval is not approved")

    validated = validate_prepared(
        prepared,
        observed_at=observed,
        minimum_seconds_remaining=SUBMIT_SAFETY_FLOOR_SECONDS,
    )
    for field in ("intent_id", "prediction_id", "paper_order_id"):
        approved_value = str(approval.get(field) or "")
        if approved_value != str(validated[field]):
            raise ApprovalError(f"approved {field} mismatch")

    approved_request_sha = str(approval.get("request_sha256") or "")
    if approved_request_sha != str(validated["request_sha256"]):
        raise ApprovalError("approved request changed")

    try:
        approved_at = _utc(datetime.fromisoformat(str(approval["approved_at"])))
        expires_at = _utc(datetime.fromisoformat(str(approval["expires_at"])))
    except (KeyError, ValueError, TypeError) as exc:
        raise ApprovalError("approval timestamps invalid") from exc
    if approved_at >= expires_at:
        raise ApprovalError("approval timestamp invalid")
    if approved_at > observed:
        raise ApprovalError("approval timestamp is in the future")
    if observed >= expires_at:
        raise ApprovalError("approval expired")

    return {
        **validated,
        "approved_at": approved_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "callback_query_id": str(approval.get("callback_query_id") or ""),
        "telegram_user_id": int(approval.get("telegram_user_id", 0)),
        "telegram_chat_id": int(approval.get("telegram_chat_id", 0)),
    }

