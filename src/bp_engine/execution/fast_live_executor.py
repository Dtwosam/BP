from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import polymarket

from bp_engine.execution.fast_live import (
    FAST_LIVE_TARGET_NOTIONAL_USD,
    FastLiveError,
    FastLiveRetryableError,
    marketable_depth,
)


class FastBookCache(Protocol):
    def snapshot(self, token_id: str) -> tuple[tuple[str, str], ...] | None: ...


class FastLiveClient(Protocol):
    def get_order_book(self, *, token_id: str) -> object: ...
    def create_limit_order(
        self,
        *,
        token_id: str,
        price: Decimal,
        size: Decimal,
        side: str,
    ) -> object: ...
    def post_order(self, signed_order: object) -> object: ...
    def cancel_order(self, *, order_id: str) -> object: ...
    def list_open_orders(self) -> object: ...
    def list_account_trades(self) -> object: ...


@dataclass(frozen=True)
class SafetySnapshot:
    observed_at: datetime
    geoblock_blocked: bool
    geoblock_country: str
    open_order_count: int
    collateral_balance_usd: Decimal

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("safety snapshot timestamp must be timezone-aware")


@dataclass(frozen=True)
class PreparedFastLiveOrder:
    intent_id: str
    request_sha256: str
    token_id: str
    limit_price: Decimal
    requested_shares: Decimal
    target_notional_usd: Decimal
    signed_order: object
    prepared_at: datetime
    sign_latency_ms: float


class SafetyCache:
    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot: SafetySnapshot | None = None

    def replace(self, snapshot: SafetySnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

    def current(self) -> SafetySnapshot | None:
        with self._lock:
            return self._snapshot


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise FastLiveError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise FastLiveError(f"{name} must be finite")
    return result


def _trade_status_text(value: object) -> str:
    raw = getattr(value, "value", value)
    text = str(raw).upper()
    if text.startswith("TRADE_STATUS_"):
        text = text[len("TRADE_STATUS_") :]
    return text


def _official_order_sample(
    client: FastLiveClient,
    *,
    order_id: str,
) -> dict[str, Any]:
    open_orders = tuple(client.list_open_orders().iter_items())
    trades = tuple(client.list_account_trades().iter_items())
    matches: list[dict[str, str]] = []
    for trade in trades:
        hit: dict[str, object] | None = None
        if str(trade.taker_order_id) == order_id:
            hit = {
                "shares": _decimal(trade.size, "trade size"),
                "price": _decimal(trade.price, "trade price"),
                "status": _trade_status_text(trade.status),
            }
        for maker in trade.maker_orders:
            if str(maker.order_id) != order_id:
                continue
            if hit is not None:
                raise FastLiveError(
                    "order appears more than once in a single official trade"
                )
            hit = {
                "shares": _decimal(maker.matched_amount, "maker matched amount"),
                "price": _decimal(maker.price, "maker price"),
                "status": _trade_status_text(trade.status),
            }
        if hit is None:
            continue
        matches.append(
            {
                "trade_id": str(trade.id),
                "shares": format(Decimal(hit["shares"]), "f"),
                "price": format(Decimal(hit["price"]), "f"),
                "status": str(hit["status"]),
            }
        )
    matches.sort(key=lambda row: row["trade_id"])
    open_ids = sorted(str(order.id) for order in open_orders)
    return {
        "order_still_open": order_id in open_ids,
        "open_order_count": len(open_ids),
        "matching_trades": matches,
    }


def probe_official_order_state(
    client: FastLiveClient,
    *,
    order_id: str,
    requested_shares: Decimal,
    stability_seconds: float = 3.0,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    first = _official_order_sample(client, order_id=order_id)
    sleep_fn(stability_seconds)
    second = _official_order_sample(client, order_id=order_id)
    stable = first == second
    confirmed = [
        row for row in second["matching_trades"]
        if row["status"] == "CONFIRMED"
    ]
    nonfinal = [
        row for row in second["matching_trades"]
        if row["status"] != "CONFIRMED"
    ]
    confirmed_shares = sum(
        (Decimal(row["shares"]) for row in confirmed),
        Decimal("0"),
    )
    confirmed_notional = sum(
        (
            Decimal(row["shares"]) * Decimal(row["price"])
            for row in confirmed
        ),
        Decimal("0"),
    )
    if confirmed_shares > requested_shares:
        raise FastLiveError("confirmed fill exceeds requested shares")
    fill_state = (
        "order_still_open"
        if second["order_still_open"]
        else "fill_observed_not_final"
        if nonfinal
        else "confirmed_fill"
        if confirmed
        else "zero_fill_observed"
    )
    return {
        "snapshot_stable": stable,
        "order_still_open": second["order_still_open"],
        "open_order_count": second["open_order_count"],
        "matching_trade_count": len(second["matching_trades"]),
        "matching_trades": second["matching_trades"],
        "confirmed_filled_shares": format(confirmed_shares, "f"),
        "confirmed_filled_notional_usd": format(
            confirmed_notional,
            "f",
        ),
        "fill_state": fill_state,
        "official_reconciliation_complete": (
            stable
            and not second["order_still_open"]
            and not nonfinal
            and second["open_order_count"] == 0
        ),
    }


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise FastLiveError("fast live state directory invalid")
    os.chmod(path, 0o700)


def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_replace_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        + "\n"
    ).encode()
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FastLiveError("fast live state JSON invalid") from exc
    if not isinstance(payload, dict):
        raise FastLiveError("fast live state JSON must contain an object")
    return payload


def execute_with_bounded_pre_attempt_retry(
    executor: FastLiveExecutor,
    verified: dict[str, Any],
    *,
    prepared_order: PreparedFastLiveOrder | None = None,
    now_fn=_utc_now,
    sleep_fn=time.sleep,
    retry_sleep_seconds: float = 0.02,
) -> dict[str, Any]:
    expires = datetime.fromisoformat(str(verified.get("expires_at") or "")).astimezone(UTC)
    retries = 0
    last_reason = ""
    while True:
        try:
            if prepared_order is None:
                result = executor.execute(verified)
            else:
                result = executor.execute(
                    verified,
                    prepared_order=prepared_order,
                )
            result["pre_attempt_retry_count"] = retries
            return result
        except FastLiveRetryableError as exc:
            attempt_path = (
                executor.attempt_path_for(verified)
                if hasattr(executor, "attempt_path_for")
                else executor.attempt_path
            )
            if attempt_path.exists():
                raise
            retries += 1
            last_reason = str(exc)
            remaining = (expires - now_fn()).total_seconds()
            if remaining <= max(0.01, retry_sleep_seconds):
                return {
                    "status": "pre_attempt_retry_exhausted",
                    "intent_id": str(verified.get("intent_id") or ""),
                    "prediction_id": str(verified.get("prediction_id") or ""),
                    "paper_order_id": str(verified.get("paper_order_id") or ""),
                    "request_sha256": str(verified.get("request_sha256") or ""),
                    "reason": last_reason,
                    "pre_attempt_retry_count": retries,
                    "network_submission_attempt_consumed": False,
                    "real_order_submitted": False,
                    "external_order_id": None,
                }
            sleep_fn(min(retry_sleep_seconds, max(0.0, remaining - 0.01)))


class FastLiveExecutor:
    def __init__(
        self,
        *,
        client: FastLiveClient,
        safety_cache: SafetyCache,
        book_cache: FastBookCache | None = None,
        state_root: Path,
        kill_switch_path: Path,
        max_safety_age_seconds: Decimal = Decimal("1"),
        order_ttl_seconds: Decimal = Decimal("2"),
        official_stability_seconds: float = 3.0,
        official_probe_attempts: int = 3,
        continuous_session: bool = False,
        now_fn=_utc_now,
    ) -> None:
        self._client = client
        self._safety_cache = safety_cache
        self._book_cache = book_cache
        self._state_root = state_root
        self._kill_switch_path = kill_switch_path
        self._max_safety_age_seconds = max_safety_age_seconds
        self._order_ttl_seconds = order_ttl_seconds
        self._official_stability_seconds = official_stability_seconds
        if official_probe_attempts < 1 or official_probe_attempts > 5:
            raise ValueError("official_probe_attempts must be within 1..5")
        self._official_probe_attempts = official_probe_attempts
        self._continuous_session = continuous_session
        self._now_fn = now_fn
        _ensure_private_dir(state_root)

    @property
    def attempt_path(self) -> Path:
        return self._state_root / "attempt.json"

    @property
    def result_path(self) -> Path:
        return self._state_root / "result.json"

    def _intent_state_root(self, verified: dict[str, Any]) -> Path:
        if not self._continuous_session:
            return self._state_root
        intent_id = str(verified.get("intent_id") or "").strip()
        request_hash = str(verified.get("request_sha256") or "").strip()
        if not intent_id or not request_hash:
            raise FastLiveError("fast live attempt identity missing")
        key = hashlib.sha256(
            (intent_id + "\0" + request_hash).encode("utf-8")
        ).hexdigest()
        attempts_root = self._state_root / "attempts"
        _ensure_private_dir(attempts_root)
        intent_root = attempts_root / key
        _ensure_private_dir(intent_root)
        return intent_root

    def attempt_path_for(self, verified: dict[str, Any]) -> Path:
        return self._intent_state_root(verified) / "attempt.json"

    def result_path_for(self, verified: dict[str, Any]) -> Path:
        return self._intent_state_root(verified) / "result.json"

    def _kill_switch_engaged(self) -> bool:
        try:
            return self._kill_switch_path.exists()
        except OSError:
            return True

    def _reengage_kill_switch(self, reason: str) -> None:
        self._kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                self._kill_switch_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return
        try:
            os.write(fd, (reason.strip() + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def _require_fresh_safety(self) -> SafetySnapshot:
        snapshot = self._safety_cache.current()
        if snapshot is None:
            raise FastLiveRetryableError("fast live safety snapshot missing")
        now = self._now_fn()
        age = Decimal(str((now - snapshot.observed_at.astimezone(UTC)).total_seconds()))
        if age < 0 or age > self._max_safety_age_seconds:
            raise FastLiveRetryableError("fast live safety snapshot stale")
        if snapshot.geoblock_blocked:
            raise FastLiveError("fast live geoblock blocked")
        if snapshot.geoblock_country != "ZA":
            raise FastLiveError("fast live executor country mismatch")
        if snapshot.open_order_count != 0:
            raise FastLiveError("fast live official open orders present")
        if snapshot.collateral_balance_usd < FAST_LIVE_TARGET_NOTIONAL_USD:
            raise FastLiveError("fast live insufficient collateral")
        return snapshot

    def _cancel_and_probe(
        self,
        *,
        order_id: str,
        requested_shares: Decimal,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            cancel = self._client.cancel_order(order_id=order_id)
            if isinstance(cancel, polymarket.CancelOrdersResponse):
                cancellation = {
                    "cancelled": order_id in cancel.canceled,
                    "not_cancelled": str(
                        cancel.not_canceled.get(order_id, "")
                    ),
                }
            else:
                cancellation = {
                    "cancelled": False,
                    "not_cancelled": "unexpected cancellation response",
                }
        except Exception:
            cancellation = {
                "cancelled": False,
                "not_cancelled": "cancellation failed",
            }

        official: dict[str, Any] = {
            "official_reconciliation_complete": False,
            "fill_state": "official_probe_not_run",
        }
        for probe_attempt in range(1, self._official_probe_attempts + 1):
            try:
                official = probe_official_order_state(
                    self._client,
                    order_id=order_id,
                    requested_shares=requested_shares,
                    stability_seconds=self._official_stability_seconds,
                )
                official["probe_attempts"] = probe_attempt
            except Exception as exc:
                official = {
                    "official_reconciliation_complete": False,
                    "fill_state": "official_probe_failed",
                    "error": type(exc).__name__,
                    "probe_attempts": probe_attempt,
                }
            if official.get("official_reconciliation_complete") is True:
                break
        return cancellation, official

    def recover_pending_cancellations(self) -> list[dict[str, Any]]:
        roots: list[Path] = []
        if self._continuous_session:
            attempts_root = self._state_root / "attempts"
            if not attempts_root.exists():
                return []
            info = attempts_root.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FastLiveError("fast live attempts directory invalid")
            roots = [
                path
                for path in sorted(attempts_root.iterdir())
                if path.is_dir() and not path.is_symlink()
            ]
        else:
            roots = [self._state_root]

        recovered: list[dict[str, Any]] = []
        for root in roots:
            attempt_path = root / "attempt.json"
            result_path = root / "result.json"
            if not attempt_path.is_file() or not result_path.is_file():
                continue
            result = _read_json(result_path)
            if result.get("cancellation_pending") is not True:
                if result.get("recovery_result_publish_pending") is True:
                    recovered.append(result)
                continue
            if (
                result.get("status") != "accepted"
                or result.get("network_submission_attempt_consumed") is not True
                or result.get("real_order_submitted") is not True
            ):
                raise FastLiveError(
                    "pending cancellation result state invalid"
                )
            order_id = str(result.get("external_order_id") or "")
            marketability = result.get("marketability")
            if not order_id or not isinstance(marketability, dict):
                raise FastLiveError(
                    "pending cancellation binding missing"
                )
            requested_shares = _decimal(
                marketability.get("requested_shares"),
                "pending cancellation requested shares",
            )
            cancellation, official = self._cancel_and_probe(
                order_id=order_id,
                requested_shares=requested_shares,
            )
            result["cancellation"] = cancellation
            result["cancellation_pending"] = False
            result["official_reconciliation"] = official
            result["recovered_pending_cancellation"] = True
            result["recovered_at"] = self._now_fn().isoformat()
            result["recovery_result_publish_pending"] = True
            _write_replace_json(result_path, result)
            recovered.append(result)
        return recovered

    def mark_recovery_result_published(
        self,
        result: dict[str, Any],
    ) -> None:
        identity = {
            "intent_id": str(result.get("intent_id") or ""),
            "request_sha256": str(result.get("request_sha256") or ""),
        }
        if (
            not identity["intent_id"]
            or not identity["request_sha256"]
        ):
            raise FastLiveError(
                "recovery result publish identity missing"
            )
        result_path = self.result_path_for(identity)
        persisted = _read_json(result_path)
        if (
            str(persisted.get("intent_id") or "")
            != identity["intent_id"]
            or str(persisted.get("request_sha256") or "")
            != identity["request_sha256"]
            or persisted.get("recovery_result_publish_pending") is not True
        ):
            raise FastLiveError(
                "recovery result publish state mismatch"
            )
        persisted["recovery_result_publish_pending"] = False
        persisted["recovery_result_published_at"] = (
            self._now_fn().isoformat()
        )
        _write_replace_json(result_path, persisted)

    def prepare_order(self, verified: dict[str, Any]) -> PreparedFastLiveOrder:
        if self.attempt_path_for(verified).exists():
            raise FastLiveError("fast live attempt already exists")
        self._require_fresh_safety()
        request = verified.get("request")
        if not isinstance(request, dict):
            raise FastLiveError("fast live request missing")
        token_id = str(request.get("token_id") or "")
        limit_price = _decimal(request.get("limit_price"), "limit_price")
        shares = _decimal(request.get("requested_shares"), "requested_shares")
        target = _decimal(request.get("target_notional_usd"), "target_notional_usd")
        if not token_id:
            raise FastLiveError("fast live token id missing")
        if str(request.get("action") or "") != "BUY":
            raise FastLiveError("fast live action must be BUY")
        if target != FAST_LIVE_TARGET_NOTIONAL_USD:
            raise FastLiveError("fast live target changed")
        prepared_at = self._now_fn()
        sign_started_ns = time.monotonic_ns()
        signed_order = self._client.create_limit_order(
            token_id=token_id,
            price=limit_price,
            size=shares,
            side="BUY",
        )
        sign_completed_ns = time.monotonic_ns()
        return PreparedFastLiveOrder(
            intent_id=str(verified["intent_id"]),
            request_sha256=str(verified["request_sha256"]),
            token_id=token_id,
            limit_price=limit_price,
            requested_shares=shares,
            target_notional_usd=target,
            signed_order=signed_order,
            prepared_at=prepared_at,
            sign_latency_ms=(sign_completed_ns - sign_started_ns) / 1_000_000,
        )

    def execute(
        self,
        verified: dict[str, Any],
        *,
        prepared_order: PreparedFastLiveOrder | None = None,
    ) -> dict[str, Any]:
        attempt_path = self.attempt_path_for(verified)
        result_path = self.result_path_for(verified)
        if attempt_path.exists():
            if result_path.exists():
                replayed = _read_json(result_path)
                if (
                    replayed.get("status") == "accepted"
                    and replayed.get("cancellation_pending") is True
                    and str(replayed.get("external_order_id") or "")
                ):
                    order_id = str(replayed["external_order_id"])
                    marketability = replayed.get("marketability")
                    if not isinstance(marketability, dict):
                        raise FastLiveError(
                            "accepted replay missing marketability"
                        )
                    requested_shares = _decimal(
                        marketability.get("requested_shares"),
                        "replayed requested shares",
                    )
                    cancellation, official = self._cancel_and_probe(
                        order_id=order_id,
                        requested_shares=requested_shares,
                    )
                    replayed["cancellation"] = cancellation
                    replayed["cancellation_pending"] = False
                    replayed["official_reconciliation"] = official
                    _write_replace_json(result_path, replayed)
                replayed["replayed_result"] = True
                return replayed
            attempt = _read_json(attempt_path)
            return {
                **attempt,
                "status": "submission_unknown",
                "accepted": False,
                "external_order_id": None,
                "network_submission_attempt_consumed": True,
                "real_order_submitted": True,
                "recovered_from_attempt_marker": True,
            }
        if self._kill_switch_engaged():
            raise FastLiveError("fast live kill switch engaged")
        safety = self._require_fresh_safety()
        request = verified.get("request")
        if not isinstance(request, dict):
            raise FastLiveError("fast live request missing")

        token_id = str(request.get("token_id") or "")
        limit_price = _decimal(request.get("limit_price"), "limit_price")
        shares = _decimal(request.get("requested_shares"), "requested_shares")
        target = _decimal(request.get("target_notional_usd"), "target_notional_usd")
        if not token_id:
            raise FastLiveError("fast live token id missing")
        if str(request.get("action") or "") != "BUY":
            raise FastLiveError("fast live action must be BUY")
        if target != FAST_LIVE_TARGET_NOTIONAL_USD:
            raise FastLiveError("fast live target changed")

        received_at = self._now_fn()
        if prepared_order is None:
            sign_started_ns = time.monotonic_ns()
            signed_order = self._client.create_limit_order(
                token_id=token_id,
                price=limit_price,
                size=shares,
                side="BUY",
            )
            sign_completed_ns = time.monotonic_ns()
            sign_latency_ms = (
                sign_completed_ns - sign_started_ns
            ) / 1_000_000
        else:
            if prepared_order.request_sha256 != str(verified["request_sha256"]):
                raise FastLiveError("prepared order request hash mismatch")
            if (
                prepared_order.token_id != token_id
                or prepared_order.limit_price != limit_price
                or prepared_order.requested_shares != shares
                or prepared_order.target_notional_usd != target
            ):
                raise FastLiveError("prepared order request changed")
            signed_order = prepared_order.signed_order
            sign_latency_ms = prepared_order.sign_latency_ms

        self._require_fresh_safety()
        if self._kill_switch_engaged():
            raise FastLiveError("fast live kill switch engaged before quote")

        quote_started_ns = time.monotonic_ns()
        cached_levels = (
            self._book_cache.snapshot(token_id)
            if self._book_cache is not None
            else None
        )
        if cached_levels is not None:
            levels = cached_levels
            quote_source = "stream"
        else:
            try:
                book = self._client.get_order_book(token_id=token_id)
            except Exception as exc:
                raise FastLiveRetryableError(
                    "fast live fresh order book unavailable"
                ) from exc
            asks = tuple(getattr(book, "asks", ()) or ())
            levels = tuple(
                (
                    getattr(level, "price", None),
                    getattr(level, "size", None),
                )
                for level in asks
            )
            quote_source = "http"
        quote_completed_ns = time.monotonic_ns()
        marketability = marketable_depth(
            levels,
            limit_price=limit_price,
            requested_shares=shares,
        )
        if marketability["marketable"] is not True:
            return {
                "status": "fresh_book_rejected",
                "intent_id": verified["intent_id"],
                "prediction_id": verified["prediction_id"],
                "paper_order_id": verified["paper_order_id"],
                "request_sha256": verified["request_sha256"],
                "marketability": marketability,
                "network_submission_attempt_consumed": False,
                "real_order_submitted": False,
                "quote_latency_ms": (
                    quote_completed_ns - quote_started_ns
                ) / 1_000_000,
                "quote_source": quote_source,
                "sign_latency_ms": sign_latency_ms,
            }

        self._require_fresh_safety()
        if self._kill_switch_engaged():
            raise FastLiveError("fast live kill switch engaged before attempt")
        attempt = {
            "schema_version": 1,
            "status": "network_submission_attempt_starting",
            "authorization_id": verified["authorization_id"],
            "intent_id": verified["intent_id"],
            "prediction_id": verified["prediction_id"],
            "paper_order_id": verified["paper_order_id"],
            "request_sha256": verified["request_sha256"],
            "started_at": self._now_fn().isoformat(),
        }
        _write_exclusive_json(attempt_path, attempt)
        if not self._continuous_session:
            self._reengage_kill_switch("fast-live-one-shot-attempt-consumed")

        post_started_at = self._now_fn()
        post_started_ns = time.monotonic_ns()
        try:
            response = self._client.post_order(signed_order)
        except Exception:
            result = {
                **attempt,
                "status": "submission_unknown",
                "accepted": False,
                "external_order_id": None,
                "network_submission_attempt_consumed": True,
                "real_order_submitted": True,
                "submission_outcome_known": False,
                "marketability": marketability,
                "safety_observed_at": safety.observed_at.isoformat(),
                "received_at": received_at.isoformat(),
                "post_started_at": post_started_at.isoformat(),
                "quote_latency_ms": (
                    quote_completed_ns - quote_started_ns
                ) / 1_000_000,
                "quote_source": quote_source,
                "sign_latency_ms": sign_latency_ms,
                "quote_to_post_ms": (
                    post_started_ns - quote_completed_ns
                ) / 1_000_000,
            }
            _write_replace_json(result_path, result)
            return result
        post_completed_ns = time.monotonic_ns()
        post_completed_at = self._now_fn()

        if isinstance(response, polymarket.RejectedOrder):
            result = {
                **attempt,
                "status": "rejected",
                "accepted": False,
                "external_order_id": None,
                "code": str(response.code),
                "message": str(response.message),
                "network_submission_attempt_consumed": True,
                "real_order_submitted": True,
            }
            _write_replace_json(result_path, result)
            return result
        if not isinstance(response, polymarket.AcceptedOrder):
            result = {
                **attempt,
                "status": "submission_unknown",
                "accepted": False,
                "external_order_id": None,
                "network_submission_attempt_consumed": True,
                "real_order_submitted": True,
            }
            _write_replace_json(result_path, result)
            return result

        order_id = str(response.order_id)
        preliminary = {
            **attempt,
            "status": "accepted",
            "accepted": True,
            "external_order_id": order_id,
            "initial_order_status": str(response.status),
            "network_submission_attempt_consumed": True,
            "real_order_submitted": True,
            "cancellation_pending": True,
            "marketability": marketability,
            "safety_observed_at": safety.observed_at.isoformat(),
            "received_at": received_at.isoformat(),
            "post_started_at": post_started_at.isoformat(),
            "post_completed_at": post_completed_at.isoformat(),
            "quote_latency_ms": (
                quote_completed_ns - quote_started_ns
            ) / 1_000_000,
            "quote_source": quote_source,
            "sign_latency_ms": sign_latency_ms,
            "post_latency_ms": (
                post_completed_ns - post_started_ns
            ) / 1_000_000,
            "quote_to_post_ms": (
                post_started_ns - quote_completed_ns
            ) / 1_000_000,
        }
        _write_replace_json(result_path, preliminary)

        time.sleep(float(self._order_ttl_seconds))
        cancellation, official = self._cancel_and_probe(
            order_id=order_id,
            requested_shares=shares,
        )

        result = {
            **attempt,
            "status": "accepted",
            "accepted": True,
            "external_order_id": order_id,
            "initial_order_status": str(response.status),
            "network_submission_attempt_consumed": True,
            "real_order_submitted": True,
            "cancellation_pending": False,
            "marketability": marketability,
            "cancellation": cancellation,
            "official_reconciliation": official,
            "safety_observed_at": safety.observed_at.isoformat(),
            "received_at": received_at.isoformat(),
            "post_started_at": post_started_at.isoformat(),
            "post_completed_at": post_completed_at.isoformat(),
            "quote_latency_ms": (
                quote_completed_ns - quote_started_ns
            ) / 1_000_000,
            "quote_source": quote_source,
            "sign_latency_ms": sign_latency_ms,
            "post_latency_ms": (
                post_completed_ns - post_started_ns
            ) / 1_000_000,
            "quote_to_post_ms": (
                post_started_ns - quote_completed_ns
            ) / 1_000_000,
        }
        _write_replace_json(result_path, result)
        return result
