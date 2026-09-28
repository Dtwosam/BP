from __future__ import annotations

import json
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from websockets.sync.client import connect

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class FastBookStreamError(RuntimeError):
    pass


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FastBookStreamError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise FastBookStreamError(f"{name} must be finite")
    return result


class StreamingBookCache:
    def __init__(
        self,
        *,
        url: str = MARKET_WS_URL,
        heartbeat_seconds: float = 10.0,
        healthy_seconds: float = 15.0,
        quote_fresh_seconds: float = 0.25,
    ) -> None:
        self._url = url
        self._heartbeat_seconds = heartbeat_seconds
        self._healthy_seconds = healthy_seconds
        self._quote_fresh_seconds = quote_fresh_seconds
        self._lock = threading.Lock()
        self._desired: set[str] = set()
        self._subscribed: set[str] = set()
        self._asks: dict[str, dict[Decimal, Decimal]] = {}
        self._initialized: set[str] = set()
        self._token_activity: dict[str, float] = {}
        self._connected = False
        self._last_activity = 0.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="bp-fast-live-book-stream",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=3)

    def subscribe(self, token_ids: tuple[str, ...] | list[str]) -> None:
        normalized = {
            str(token).strip()
            for token in token_ids
            if str(token).strip()
        }
        if not normalized:
            return
        with self._lock:
            self._desired.update(normalized)
        self._wake.set()

    def snapshot(self, token_id: str) -> tuple[tuple[str, str], ...] | None:
        token = str(token_id).strip()
        now = time.monotonic()
        with self._lock:
            token_activity = self._token_activity.get(token)
            healthy = (
                self._connected
                and token in self._initialized
                and token_activity is not None
                and now - self._last_activity <= self._healthy_seconds
                and now - token_activity <= self._quote_fresh_seconds
            )
            if not healthy:
                return None
            levels = self._asks.get(token, {})
            return tuple(
                (format(price, "f"), format(size, "f"))
                for price, size in sorted(levels.items())
                if size > 0
            )

    def _set_disconnected(self) -> None:
        with self._lock:
            self._connected = False
            self._subscribed.clear()
            self._initialized.clear()
            self._asks.clear()
            self._token_activity.clear()

    def _desired_tokens(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._desired))

    def _mark_connected(self) -> None:
        with self._lock:
            self._connected = True
            self._last_activity = time.monotonic()

    def _mark_activity(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()

    def _replace_book(self, payload: dict[str, Any]) -> None:
        token = str(payload.get("asset_id") or "").strip()
        asks = payload.get("asks")
        if not token or not isinstance(asks, list):
            raise FastBookStreamError("market book payload invalid")
        levels: dict[Decimal, Decimal] = {}
        for level in asks:
            if not isinstance(level, dict):
                raise FastBookStreamError("market book ask invalid")
            price = _decimal(level.get("price"), "book ask price")
            size = _decimal(level.get("size"), "book ask size")
            if not Decimal("0") < price <= Decimal("1") or size < 0:
                raise FastBookStreamError("market book ask out of range")
            if size > 0:
                levels[price] = size
        with self._lock:
            if token in self._desired:
                observed = time.monotonic()
                self._asks[token] = levels
                self._initialized.add(token)
                self._token_activity[token] = observed
                self._last_activity = observed

    def _apply_price_changes(self, payload: dict[str, Any]) -> None:
        changes = payload.get("price_changes")
        if not isinstance(changes, list):
            return
        touched_tokens: set[str] = set()
        with self._lock:
            for change in changes:
                if not isinstance(change, dict):
                    continue
                token = str(change.get("asset_id") or "").strip()
                side = str(change.get("side") or "").upper()
                if (
                    token not in self._desired
                    or token not in self._initialized
                    or side != "SELL"
                ):
                    continue
                price = _decimal(change.get("price"), "price change price")
                size = _decimal(change.get("size"), "price change size")
                if not Decimal("0") < price <= Decimal("1") or size < 0:
                    raise FastBookStreamError("price change out of range")
                levels = self._asks.setdefault(token, {})
                if size == 0:
                    levels.pop(price, None)
                else:
                    levels[price] = size
                touched_tokens.add(token)
            if touched_tokens:
                observed = time.monotonic()
                for token in touched_tokens:
                    self._token_activity[token] = observed
                self._last_activity = observed

    def _handle_payload(self, raw: str) -> None:
        if raw == "PONG":
            self._mark_activity()
            return
        payload = json.loads(raw)
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if not isinstance(message, dict):
                continue
            event_type = str(message.get("event_type") or "")
            if event_type == "book":
                self._replace_book(message)
            elif event_type == "price_change":
                self._apply_price_changes(message)

    def _run_connection(self, tokens: tuple[str, ...]) -> None:
        with connect(self._url, open_timeout=3, close_timeout=1) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "assets_ids": list(tokens),
                        "type": "market",
                        "custom_feature_enabled": True,
                    },
                    separators=(",", ":"),
                )
            )
            with self._lock:
                self._subscribed = set(tokens)
            self._mark_connected()
            last_ping = time.monotonic()

            while not self._stop.is_set():
                desired = set(self._desired_tokens())
                with self._lock:
                    additions = sorted(desired - self._subscribed)
                if additions:
                    websocket.send(
                        json.dumps(
                            {
                                "assets_ids": additions,
                                "operation": "subscribe",
                                "custom_feature_enabled": True,
                            },
                            separators=(",", ":"),
                        )
                    )
                    with self._lock:
                        self._subscribed.update(additions)

                now = time.monotonic()
                if now - last_ping >= self._heartbeat_seconds:
                    websocket.send("PING")
                    last_ping = now
                try:
                    raw = websocket.recv(timeout=0.1)
                except TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                self._handle_payload(str(raw))

    def _run(self) -> None:
        while not self._stop.is_set():
            tokens = self._desired_tokens()
            if not tokens:
                self._wake.wait(timeout=0.5)
                self._wake.clear()
                continue
            try:
                self._run_connection(tokens)
            except Exception:
                self._set_disconnected()
                if not self._stop.wait(0.1):
                    continue
