from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.cloud import pubsub_v1
from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.execution.fast_live import (
    FAST_LIVE_PURPOSE,
    FAST_LIVE_RESULT_MAX_AGE_SECONDS,
    FAST_LIVE_RESULT_PURPOSE,
    FAST_LIVE_RESULT_STALL_SECONDS,
    FAST_LIVE_WARMUP_PURPOSE,
    create_approval_message,
    create_envelope,
    create_prepare_message,
    create_warmup_message,
    load_private_json,
    verify_result_message,
    verify_runtime_authorization,
)
from bp_engine.execution.fast_live import (
    request_sha256 as fast_live_request_sha256,
)
from bp_engine.execution.fast_live_prepare import (
    prepare_fast_live_candidate,
    preview_fast_live_candidate,
)
from bp_engine.execution.fast_live_result import (
    record_fast_live_official_reconciliation,
    record_fast_live_result,
    settle_fast_live_position_if_resolved,
)
from bp_engine.execution.live import InterlockDecision
from bp_engine.execution.telegram_transport import load_transport_key_file
from bp_engine.storage import schema


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the continuous Telegram-approved fast-live V3 source."
    )
    parser.add_argument("--env-file", default=None)
    parser.add_argument(
        "--project-state",
        type=Path,
        default=Path("/etc/bp-fast-live/PROJECT_STATE.json"),
    )
    parser.add_argument(
        "--runtime-authorization",
        type=Path,
        default=Path("/etc/bp-fast-live/authorization.json"),
    )
    parser.add_argument(
        "--transport-key-file",
        type=Path,
        default=Path("/etc/bp-fast-live/transport.key"),
    )
    parser.add_argument("--transport-key-id", required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--gcp-project", required=True)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--result-subscription-id", required=True)
    parser.add_argument("--official-open-order-count", type=int, required=True)
    parser.add_argument("--collateral-balance-usd", required=True)
    parser.add_argument("--poll-seconds", type=float, default=0.02)
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=Path("/var/lib/bp/phase15-fast-live/published"),
    )
    parser.add_argument(
        "--telegram-prepare-state-root",
        type=Path,
        default=Path("/var/lib/bp/phase15-fast-live/telegram-prepare"),
    )
    parser.add_argument(
        "--telegram-approval-state-root",
        type=Path,
        default=Path("/var/lib/bp/phase15-canary-telegram-approval"),
    )
    return parser.parse_args()


def _require_runtime() -> None:
    if os.environ.get("BP_PHASE15_FAST_LIVE_SOURCE_ENABLED", "no") != "yes":
        raise SystemExit("fast live source is not explicitly enabled")
    for name in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS"):
        if os.environ.get(name):
            raise SystemExit(f"{name} must not be present in fast live source")


def _telegram_approval_required() -> bool:
    return (
        os.environ.get("BP_FAST_LIVE_TELEGRAM_APPROVAL_REQUIRED", "no")
        .strip()
        .lower()
        == "yes"
    )


def _continuous_session() -> bool:
    return (
        os.environ.get("BP_FAST_LIVE_CONTINUOUS_SESSION", "no")
        .strip()
        .lower()
        == "yes"
    )


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("project state is not readable") from exc
    if not isinstance(payload, dict):
        raise SystemExit("project state must contain an object")
    return payload


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit("fast live receipt directory invalid")
    os.chmod(path, 0o700)


def _receipt_path(root: Path, intent_id: str, request_sha256: str) -> Path:
    key = hashlib.sha256(f"{intent_id}\0{request_sha256}".encode()).hexdigest()
    return root / f"{key}.json"


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("fast live receipt must contain an object")
    return payload



def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(value, encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def _telegram_run_dir(root: Path, intent_id: str) -> Path:
    key = hashlib.sha256(intent_id.encode("utf-8")).hexdigest()
    return root / "runs" / key


def _stage_telegram_candidate(
    root: Path,
    prepared: dict[str, Any],
    *,
    authorization_id: str,
) -> Path:
    _ensure_private_dir(root)
    _ensure_private_dir(root / "runs")
    intent_id = str(prepared.get("intent_id") or "")
    if not intent_id:
        raise RuntimeError("fast live Telegram intent id missing")
    run_dir = _telegram_run_dir(root, intent_id)
    _ensure_private_dir(run_dir)
    prepared_path = run_dir / "prepared.json"
    encoded = json.dumps(
        prepared,
        sort_keys=True,
        indent=2,
        default=str,
    ) + "\n"
    if prepared_path.exists():
        existing = prepared_path.read_text(encoding="utf-8")
        if existing != encoded:
            raise RuntimeError("fast live Telegram prepared candidate changed")
    else:
        prepared_path.write_text(encoded, encoding="utf-8")
        os.chmod(prepared_path, 0o600)
    normalized_authorization_id = str(authorization_id).strip()
    if not normalized_authorization_id:
        raise RuntimeError("fast live Telegram authorization id missing")
    authorization_path = run_dir / "authorization-id"
    if authorization_path.is_file():
        existing_authorization_id = authorization_path.read_text(
            encoding="utf-8"
        ).strip()
        if existing_authorization_id != normalized_authorization_id:
            raise RuntimeError(
                "fast live Telegram candidate authorization changed"
            )
    else:
        _atomic_text(
            authorization_path,
            normalized_authorization_id + "\n",
        )
    _atomic_text(root / "current-run", str(run_dir.resolve()) + "\n")
    return prepared_path


def _telegram_state_file(
    root: Path,
    preview_intent_id: str,
    name: str,
) -> Path:
    return _telegram_run_dir(root, preview_intent_id) / name


def _write_telegram_state_once(
    root: Path,
    preview_intent_id: str,
    name: str,
    payload: dict[str, Any],
) -> Path:
    path = _telegram_state_file(root, preview_intent_id, name)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        default=str,
    ) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != encoded:
            raise RuntimeError(
                f"fast live Telegram {name} state changed"
            )
        return path
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(encoded, encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    return path


def _load_telegram_state(
    root: Path,
    preview_intent_id: str,
    name: str,
) -> dict[str, Any] | None:
    path = _telegram_state_file(root, preview_intent_id, name)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"fast live Telegram {name} state invalid"
        )
    return payload


def _load_staged_telegram_candidate(
    root: Path,
    *,
    expected_authorization_id: str,
) -> dict[str, Any] | None:
    current = root / "current-run"
    if not current.is_file():
        return None
    raw = current.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    run_dir = Path(raw)
    runs_root = (root / "runs").resolve()
    try:
        run_dir.resolve().relative_to(runs_root)
    except ValueError as exc:
        raise RuntimeError("fast live Telegram current run escapes state root") from exc
    authorization_path = run_dir / "authorization-id"
    expected = str(expected_authorization_id).strip()
    if (
        not expected
        or not authorization_path.is_file()
        or authorization_path.read_text(encoding="utf-8").strip()
        != expected
    ):
        current.unlink(missing_ok=True)
        return None
    prepared_path = run_dir / "prepared.json"
    if not prepared_path.is_file():
        return None
    payload = json.loads(prepared_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("fast live Telegram prepared state invalid")
    return payload


def _clear_staged_telegram_candidate(root: Path, intent_id: str) -> None:
    current = root / "current-run"
    if not current.is_file():
        return
    raw = current.read_text(encoding="utf-8").strip()
    if not raw:
        return
    run_dir = Path(raw)
    if run_dir.resolve() == _telegram_run_dir(root, intent_id).resolve():
        current.unlink(missing_ok=True)
        return
    finalized_path = run_dir / "finalized.json"
    if not finalized_path.is_file():
        return
    finalized = _load_json(finalized_path)
    if str(finalized.get("intent_id") or "") == str(intent_id):
        current.unlink(missing_ok=True)


def _approval_record_path(root: Path, intent_id: str) -> Path:
    return root / intent_id / "approval.json"


def _publish_control_once(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    payload: dict[str, Any],
) -> str:
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    attributes = {
        "purpose": str(payload["purpose"]),
        "key_id": str(payload["key_id"]),
        "authorization_id": str(payload["authorization_id"]),
    }
    for name in ("intent_id", "request_sha256"):
        if payload.get(name):
            attributes[name] = str(payload[name])
    future = publisher.publish(topic_path, data, **attributes)
    return str(future.result(timeout=0.45))


def _publish_control_with_bounded_retry(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    payload: dict[str, Any],
) -> tuple[str, int]:
    expires_at = datetime.fromisoformat(str(payload["expires_at"])).astimezone(UTC)
    attempts = 0
    last_error: Exception | None = None
    while attempts < 3:
        attempts += 1
        if (expires_at - _utc_now()).total_seconds() <= 0.15:
            break
        try:
            return (
                _publish_control_once(
                    publisher,
                    topic_path=topic_path,
                    payload=payload,
                ),
                attempts,
            )
        except Exception as exc:
            last_error = exc
            if attempts < 3:
                time.sleep(0.025)
    raise RuntimeError(
        f"fast live control publish failed after {attempts} bounded attempts"
    ) from last_error


def _result_receipt_path(
    root: Path,
    intent_id: str,
    request_sha256: str,
) -> Path:
    return _receipt_path(root / "results", intent_id, request_sha256)


def _publication_state(
    root: Path,
    *,
    continuous_session: bool = False,
) -> str:
    attempted = False
    unresolved = False
    for path in root.glob("*.json"):
        receipt = _load_json(path)
        intent_id = str(receipt.get("intent_id") or "")
        request_hash = str(receipt.get("request_sha256") or "")
        if not intent_id or not request_hash:
            continue
        result_path = _result_receipt_path(root, intent_id, request_hash)
        if not result_path.exists():
            unresolved = True
            continue
        result_receipt = _load_json(result_path)
        if result_receipt.get("network_submission_attempt_consumed") is True:
            attempted = True
    if attempted and not continuous_session:
        return "attempt_consumed"
    if unresolved:
        return "waiting_for_result"
    return "ready"


def _pending_result_binding(root: Path) -> tuple[str, str] | None:
    for path in sorted(root.glob("*.json")):
        receipt = _load_json(path)
        intent_id = str(receipt.get("intent_id") or "").strip()
        request_hash = str(receipt.get("request_sha256") or "").strip()
        if not intent_id or not request_hash:
            continue
        if not _result_receipt_path(root, intent_id, request_hash).exists():
            return intent_id, request_hash
    return None


def _result_wait_deadline(
    root: Path,
    intent_id: str,
    request_sha256: str,
) -> datetime:
    receipt = _load_json(
        _receipt_path(root, intent_id, request_sha256)
    )
    published_raw = str(receipt.get("published_at") or "")
    if not published_raw:
        raise RuntimeError("fast live publication receipt timestamp missing")
    published_at = datetime.fromisoformat(published_raw).astimezone(UTC)
    return published_at + timedelta(
        seconds=float(FAST_LIVE_RESULT_STALL_SECONDS)
    )


def _settlement_marker_path(
    root: Path,
    *,
    intent_id: str,
) -> Path:
    results_root = root / "results"
    for path in sorted(results_root.glob("*.json")):
        receipt = _load_json(path)
        if str(receipt.get("intent_id") or "") != str(intent_id):
            continue
        official = receipt.get("official_recorded")
        if (
            isinstance(official, dict)
            and official.get("settlement_reconciliation_required") is True
        ):
            return root / "settlements" / path.name
    raise RuntimeError("fast live settlement result receipt missing")


def _pending_settlement_intent(root: Path) -> str:
    results_root = root / "results"
    if not results_root.is_dir():
        return ""
    settlements_root = root / "settlements"
    for path in sorted(results_root.glob("*.json")):
        receipt = _load_json(path)
        official = receipt.get("official_recorded")
        if not isinstance(official, dict):
            continue
        if official.get("settlement_reconciliation_required") is not True:
            continue
        if (settlements_root / path.name).is_file():
            continue
        intent_id = str(receipt.get("intent_id") or "").strip()
        if intent_id:
            return intent_id
    return ""


def _settle_until_terminal(
    *,
    engine,
    receipt_dir: Path,
    intent_id: str,
    poll_seconds: float,
) -> int:
    while True:
        settlement = settle_fast_live_position_if_resolved(
            engine=engine,
            intent_id=intent_id,
            observed_at=_utc_now(),
        )
        status = str(settlement.get("status") or "")
        if status in {"settled", "already_settled"}:
            marker_path = _settlement_marker_path(
                receipt_dir,
                intent_id=intent_id,
            )
            if not marker_path.is_file():
                _write_receipt(
                    marker_path,
                    {
                        "status": "fast_live_settlement_recorded",
                        "intent_id": intent_id,
                        "reconciliation_id": str(
                            settlement.get("reconciliation_id") or ""
                        ),
                        "settled_at": _utc_now().isoformat(),
                    },
                )
            print(
                json.dumps(
                    settlement,
                    sort_keys=True,
                    default=str,
                ),
                flush=True,
            )
            return 0
        if status != "waiting":
            print(
                json.dumps(
                    settlement,
                    sort_keys=True,
                    default=str,
                ),
                flush=True,
            )
            return 2
        time.sleep(max(poll_seconds, 0.25))


def _publish_once(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    envelope: dict[str, Any],
) -> str:
    data = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    future = publisher.publish(
        topic_path,
        data,
        purpose=FAST_LIVE_PURPOSE,
        key_id=str(envelope["key_id"]),
        authorization_id=str(envelope["authorization_id"]),
        intent_id=str(envelope["intent_id"]),
        request_sha256=str(envelope["request_sha256"]),
    )
    return str(future.result(timeout=0.45))


def _publish_with_bounded_retry(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    envelope: dict[str, Any],
) -> tuple[str, int]:
    expires_at = datetime.fromisoformat(str(envelope["expires_at"])).astimezone(UTC)
    attempts = 0
    last_error: Exception | None = None
    while attempts < 3:
        attempts += 1
        now = _utc_now()
        if (expires_at - now).total_seconds() <= 0.15:
            break
        try:
            return (
                _publish_once(
                    publisher,
                    topic_path=topic_path,
                    envelope=envelope,
                ),
                attempts,
            )
        except Exception as exc:
            last_error = exc
            if attempts < 3:
                time.sleep(0.025)
    raise RuntimeError(
        f"fast live publish failed after {attempts} bounded attempts"
    ) from last_error


def _current_warmup_market(
    engine,
    *,
    observed_at: datetime,
) -> dict[str, str] | None:
    with engine.connect() as connection:
        row = connection.execute(
            select(
                schema.polymarket_markets.c.condition_id,
                schema.polymarket_markets.c.up_token_id,
                schema.polymarket_markets.c.down_token_id,
            )
            .where(
                schema.polymarket_markets.c.horizon_seconds == 300,
                schema.polymarket_markets.c.active.is_(True),
                schema.polymarket_markets.c.closed.is_(False),
                schema.polymarket_markets.c.accepting_orders.is_(True),
                schema.polymarket_markets.c.start_at <= observed_at,
                schema.polymarket_markets.c.end_at > observed_at,
            )
            .order_by(schema.polymarket_markets.c.start_at.desc())
            .limit(1)
        ).mappings().one_or_none()
    if row is None:
        return None
    return {
        "condition_id": str(row["condition_id"]),
        "up_token_id": str(row["up_token_id"]),
        "down_token_id": str(row["down_token_id"]),
    }


def _publish_warmup_async(
    publisher: pubsub_v1.PublisherClient,
    *,
    topic_path: str,
    warmup: dict[str, Any],
) -> None:
    data = json.dumps(
        warmup,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    publisher.publish(
        topic_path,
        data,
        purpose=FAST_LIVE_WARMUP_PURPOSE,
        key_id=str(warmup["key_id"]),
        authorization_id=str(warmup["authorization_id"]),
        condition_id=str(warmup["condition_id"]),
    )


def main() -> int:
    args = _parse_args()
    _require_runtime()
    approval_required = _telegram_approval_required()
    continuous_session = _continuous_session()
    if continuous_session and not approval_required:
        raise SystemExit(
            "continuous fast live requires Telegram approval"
        )
    if not 0.02 <= args.poll_seconds <= 1:
        raise SystemExit("poll seconds must be within 0.02..1")
    if args.official_open_order_count != 0:
        raise SystemExit("official open order count must be zero")
    collateral = Decimal(args.collateral_balance_usd)
    if collateral < Decimal("5"):
        raise SystemExit("official collateral must be at least 5")

    _ensure_private_dir(args.receipt_dir)
    _ensure_private_dir(args.receipt_dir / "results")
    _ensure_private_dir(args.receipt_dir / "settlements")
    if approval_required:
        _ensure_private_dir(args.telegram_prepare_state_root)
    settings = (
        Settings(_env_file=args.env_file)
        if args.env_file
        else Settings()
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    publication_state = _publication_state(
        args.receipt_dir,
        continuous_session=continuous_session,
    )
    pending_settlement = _pending_settlement_intent(args.receipt_dir)
    if publication_state == "attempt_consumed" and not continuous_session:
        if pending_settlement:
            try:
                return _settle_until_terminal(
                    engine=engine,
                    receipt_dir=args.receipt_dir,
                    intent_id=pending_settlement,
                    poll_seconds=args.poll_seconds,
                )
            finally:
                engine.dispose()
        engine.dispose()
        return 0

    if pending_settlement:
        settlement_status = _settle_until_terminal(
            engine=engine,
            receipt_dir=args.receipt_dir,
            intent_id=pending_settlement,
            poll_seconds=args.poll_seconds,
        )
        if settlement_status != 0:
            engine.dispose()
            return settlement_status
        pending_settlement = ""

    pending_result_binding = _pending_result_binding(args.receipt_dir)
    pending_result_deadline = (
        _result_wait_deadline(
            args.receipt_dir,
            pending_result_binding[0],
            pending_result_binding[1],
        )
        if pending_result_binding is not None
        else None
    )

    key = load_transport_key_file(args.transport_key_file)
    state = _load_state(args.project_state)
    runtime = load_private_json(
        args.runtime_authorization,
        label="fast live runtime authorization",
    )
    runtime_expires_raw = datetime.fromisoformat(
        str(runtime.get("expires_at") or "")
    )
    if (
        runtime_expires_raw.tzinfo is None
        or runtime_expires_raw.utcoffset() is None
    ):
        engine.dispose()
        raise SystemExit(
            "runtime authorization expires_at must be timezone-aware"
        )
    runtime_expires_at = runtime_expires_raw.astimezone(UTC)
    startup_observed_at = _utc_now()
    result_reconciliation_deadline = runtime_expires_at + timedelta(
        seconds=float(FAST_LIVE_RESULT_MAX_AGE_SECONDS) + 5.0
    )
    reconciliation_only = False
    if startup_observed_at >= runtime_expires_at:
        if pending_result_binding is None:
            print(
                json.dumps(
                    {
                        "status": "fast_live_authorization_expired",
                        "reconciliation_only": False,
                        "observed_at": startup_observed_at.isoformat(),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            engine.dispose()
            return 0
        reconciliation_only = True
        authorization_validation_observed_at = (
            runtime_expires_at - timedelta(microseconds=1)
        )
    else:
        authorization_validation_observed_at = startup_observed_at

    verified_runtime = verify_runtime_authorization(
        runtime,
        state=state,
        expected_main=args.expected_main,
        observed_at=authorization_validation_observed_at,
        requires_telegram_approval=approval_required,
        continuous_session=continuous_session,
    )
    activated_at = datetime.fromisoformat(str(verified_runtime["issued_at"]))
    if activated_at.tzinfo is None or activated_at.utcoffset() is None:
        raise SystemExit("runtime authorization issued_at must be timezone-aware")
    activated_at = activated_at.astimezone(UTC)

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(args.gcp_project, args.topic_id)
    result_subscriber = pubsub_v1.SubscriberClient()
    result_subscription_path = result_subscriber.subscription_path(
        args.gcp_project,
        args.result_subscription_id,
    )
    interlock = InterlockDecision(eligible=True, reasons=())
    warmed_condition_id = ""
    next_warmup_check = 0.0
    result_event = threading.Event()
    result_state_lock = threading.Lock()
    result_record_lock = threading.Lock()
    result_state: dict[str, Any] = {
        "awaited_intent_id": (
            pending_result_binding[0]
            if pending_result_binding is not None
            else ""
        ),
        "awaited_request_sha256": (
            pending_result_binding[1]
            if pending_result_binding is not None
            else ""
        ),
        "awaited_result_deadline": pending_result_deadline,
        "result_stall_reported": False,
        "network_submission_attempt_consumed": None,
        "result": None,
        "settlement_required": False,
        "settlement_intent_id": "",
    }

    def result_callback(message: object) -> None:
        observed = _utc_now()
        try:
            data = bytes(getattr(message, "data", b""))
            if not data or len(data) > 256 * 1024:
                raise RuntimeError("fast live result message size invalid")
            payload = json.loads(data.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("fast live result message invalid")
            attributes = {
                str(key): str(value)
                for key, value in dict(
                    getattr(message, "attributes", {}) or {}
                ).items()
            }
            if attributes.get("purpose") != FAST_LIVE_RESULT_PURPOSE:
                raise RuntimeError("fast live result purpose mismatch")
            for name in (
                "key_id",
                "authorization_id",
                "intent_id",
                "request_sha256",
            ):
                if attributes.get(name) != str(payload.get(name) or ""):
                    raise RuntimeError(
                        f"fast live result attribute mismatch: {name}"
                    )

            result = verify_result_message(
                payload,
                key=key,
                expected_key_id=args.transport_key_id,
                expected_authorization_id=str(
                    verified_runtime["authorization_id"]
                ),
                observed_at=observed,
            )
            result_path = _result_receipt_path(
                args.receipt_dir,
                str(result["intent_id"]),
                str(result["request_sha256"]),
            )
            with result_record_lock:
                result_replayed = result_path.is_file()
                if result_replayed:
                    durable_receipt = _load_json(result_path)
                    durable_attempted = (
                        durable_receipt.get(
                            "network_submission_attempt_consumed"
                        )
                        is True
                    )
                    incoming_attempted = (
                        result.get(
                            "network_submission_attempt_consumed"
                        )
                        is True
                    )
                    if (
                        str(durable_receipt.get("intent_id") or "")
                        != str(result["intent_id"])
                        or str(
                            durable_receipt.get("request_sha256") or ""
                        )
                        != str(result["request_sha256"])
                        or str(
                            durable_receipt.get("execution_status") or ""
                        )
                        != str(result.get("status") or "")
                        or durable_attempted != incoming_attempted
                    ):
                        raise RuntimeError(
                            "fast live replayed result changed"
                        )
                    recorded = durable_receipt.get("recorded")
                    official_recorded = durable_receipt.get(
                        "official_recorded"
                    )
                else:
                    result_observed_at = datetime.fromisoformat(
                        str(payload["created_at"])
                    ).astimezone(UTC)
                    recorded = record_fast_live_result(
                        engine=engine,
                        result=result,
                        observed_at=result_observed_at,
                    )
                    official_recorded: dict[str, object] | None = None
                    official = result.get("official_reconciliation")
                    if (
                        isinstance(official, dict)
                        and official.get(
                            "official_reconciliation_complete"
                        )
                        is True
                        and result.get("status") == "accepted"
                    ):
                        official_recorded = (
                            record_fast_live_official_reconciliation(
                                engine=engine,
                                result=result,
                                official=official,
                                observed_at=result_observed_at
                                + timedelta(microseconds=1),
                            )
                        )
                    _write_receipt(
                        result_path,
                        {
                            "status": "fast_live_result_recorded",
                            "intent_id": result["intent_id"],
                            "request_sha256": result["request_sha256"],
                            "network_submission_attempt_consumed": result.get(
                                "network_submission_attempt_consumed"
                            )
                            is True,
                            "execution_status": result.get("status"),
                            "recorded": recorded,
                            "official_recorded": official_recorded,
                            "recorded_at": observed.isoformat(),
                        },
                    )
            with result_state_lock:
                is_current_result = (
                    str(result.get("intent_id") or "")
                    == str(result_state["awaited_intent_id"])
                    and str(result.get("request_sha256") or "")
                    == str(result_state["awaited_request_sha256"])
                )
                if is_current_result:
                    result_state["network_submission_attempt_consumed"] = (
                        result.get("network_submission_attempt_consumed") is True
                    )
                    result_state["result"] = result
                    result_state["settlement_required"] = (
                        isinstance(official_recorded, dict)
                        and official_recorded.get(
                            "settlement_reconciliation_required"
                        )
                        is True
                    )
                    result_state["settlement_intent_id"] = str(
                        result.get("intent_id") or ""
                    )
                    result_event.set()
            print(
                json.dumps(
                    {
                        "status": (
                            "fast_live_result_replayed"
                            if result_replayed
                            else "fast_live_result_recorded"
                        ),
                        "execution_status": result.get("status"),
                        "intent_id": result.get("intent_id"),
                        "network_submission_attempt_consumed": result.get(
                            "network_submission_attempt_consumed"
                        )
                        is True,
                        "recorded": recorded,
                        "official_recorded": official_recorded,
                    },
                    sort_keys=True,
                    default=str,
                ),
                flush=True,
            )
            message.ack()
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "fast_live_result_record_failed",
                        "error": type(exc).__name__,
                        "observed_at": observed.isoformat(),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            message.nack()

    result_future = result_subscriber.subscribe(
        result_subscription_path,
        callback=result_callback,
    )
    waiting_for_result = publication_state == "waiting_for_result"
    settlement_intent_id = ""
    if reconciliation_only:
        print(
            json.dumps(
                {
                    "status": "fast_live_result_reconciliation_only",
                    "authorization_expired": True,
                    "intent_id": str(result_state["awaited_intent_id"]),
                    "request_sha256": str(
                        result_state["awaited_request_sha256"]
                    ),
                    "result_deadline": (
                        pending_result_deadline.isoformat()
                        if pending_result_deadline is not None
                        else None
                    ),
                    "observed_at": _utc_now().isoformat(),
                },
                sort_keys=True,
            ),
            flush=True,
        )

    try:
        while True:
            if settlement_intent_id:
                settlement_status = _settle_until_terminal(
                    engine=engine,
                    receipt_dir=args.receipt_dir,
                    intent_id=settlement_intent_id,
                    poll_seconds=args.poll_seconds,
                )
                if settlement_status != 0:
                    return settlement_status
                settlement_intent_id = ""
                waiting_for_result = False
                continue
            if waiting_for_result:
                now = _utc_now()
                with result_state_lock:
                    awaited_result_deadline = result_state.get(
                        "awaited_result_deadline"
                    )
                effective_result_deadline = result_reconciliation_deadline
                if isinstance(awaited_result_deadline, datetime):
                    effective_result_deadline = min(
                        effective_result_deadline,
                        awaited_result_deadline,
                    )
                if now >= effective_result_deadline:
                    with result_state_lock:
                        stall_reported = bool(
                            result_state["result_stall_reported"]
                        )
                        if not stall_reported:
                            result_state["result_stall_reported"] = True
                    if not stall_reported:
                        print(
                            json.dumps(
                                {
                                    "status": (
                                        "fast_live_result_reconciliation_stalled"
                                    ),
                                    "authorization_expired": (
                                        now >= runtime_expires_at
                                    ),
                                    "intent_id": str(
                                        result_state["awaited_intent_id"]
                                    ),
                                    "request_sha256": str(
                                        result_state[
                                            "awaited_request_sha256"
                                        ]
                                    ),
                                    "result_deadline": (
                                        effective_result_deadline.isoformat()
                                    ),
                                    "network_submission_attempt_consumed": None,
                                    "real_order_submitted": None,
                                    "observed_at": now.isoformat(),
                                },
                                sort_keys=True,
                            ),
                            flush=True,
                        )
                elif now >= runtime_expires_at:
                    print(
                        json.dumps(
                            {
                                "status": (
                                    "fast_live_result_reconciliation_grace"
                                ),
                                "authorization_expired": True,
                                "grace_deadline": (
                                    result_reconciliation_deadline.isoformat()
                                ),
                                "network_submission_attempt_consumed": None,
                                "real_order_submitted": None,
                                "observed_at": now.isoformat(),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                if not result_event.wait(timeout=args.poll_seconds):
                    continue
                result_event.clear()
                with result_state_lock:
                    consumed = (
                        result_state["network_submission_attempt_consumed"] is True
                    )
                    settlement_required = (
                        result_state["settlement_required"] is True
                    )
                    completed_intent_id = str(
                        result_state["settlement_intent_id"]
                    )
                    result_state["awaited_intent_id"] = ""
                    result_state["awaited_request_sha256"] = ""
                    result_state["awaited_result_deadline"] = None
                    result_state["result_stall_reported"] = False
                    result_state["network_submission_attempt_consumed"] = None
                    result_state["result"] = None
                    result_state["settlement_required"] = False
                    result_state["settlement_intent_id"] = ""
                if approval_required:
                    _clear_staged_telegram_candidate(
                        args.telegram_prepare_state_root,
                        completed_intent_id,
                    )
                if consumed:
                    if settlement_required:
                        settlement_intent_id = completed_intent_id
                        continue
                    if not continuous_session:
                        return 0
                waiting_for_result = False
                continue
            observed = _utc_now()
            if observed >= runtime_expires_at:
                print(
                    json.dumps(
                        {
                            "status": "fast_live_authorization_expired",
                            "network_submission_attempt_consumed": False,
                            "real_order_submitted": False,
                            "observed_at": observed.isoformat(),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 0
            now_monotonic = time.monotonic()
            if now_monotonic >= next_warmup_check:
                next_warmup_check = now_monotonic + 0.5
                warm_market = _current_warmup_market(
                    engine,
                    observed_at=observed,
                )
                if (
                    warm_market is not None
                    and warm_market["condition_id"] != warmed_condition_id
                ):
                    state_now = _load_state(args.project_state)
                    runtime_now = load_private_json(
                        args.runtime_authorization,
                        label="fast live runtime authorization",
                    )
                    verify_runtime_authorization(
                        runtime_now,
                        state=state_now,
                        expected_main=args.expected_main,
                        observed_at=observed,
                        requires_telegram_approval=approval_required,
                        continuous_session=continuous_session,
                    )
                    warmup = create_warmup_message(
                        condition_id=warm_market["condition_id"],
                        token_ids=(
                            warm_market["up_token_id"],
                            warm_market["down_token_id"],
                        ),
                        runtime_authorization=runtime_now,
                        key=key,
                        key_id=args.transport_key_id,
                        created_at=observed,
                    )
                    _publish_warmup_async(
                        publisher,
                        topic_path=topic_path,
                        warmup=warmup,
                    )
                    warmed_condition_id = warm_market["condition_id"]

            if approval_required:
                preview = _load_staged_telegram_candidate(
                    args.telegram_prepare_state_root,
                    expected_authorization_id=str(
                        verified_runtime["authorization_id"]
                    ),
                )
                if preview is None:
                    preview = preview_fast_live_candidate(
                        engine=engine,
                        activated_at=activated_at,
                        observed_at=observed,
                    )
                preview_status = str(preview.get("status") or "")
                if preview_status == "waiting":
                    time.sleep(args.poll_seconds)
                    continue
                if preview_status in {"skipped", "blocked"}:
                    print(
                        json.dumps(
                            preview,
                            sort_keys=True,
                            default=str,
                        ),
                        flush=True,
                    )
                    time.sleep(args.poll_seconds)
                    continue
                if preview_status != "prepared":
                    print(
                        json.dumps(
                            preview,
                            sort_keys=True,
                            default=str,
                        ),
                        flush=True,
                    )
                    return 2

                state = _load_state(args.project_state)
                runtime = load_private_json(
                    args.runtime_authorization,
                    label="fast live runtime authorization",
                )
                verify_runtime_authorization(
                    runtime,
                    state=state,
                    expected_main=args.expected_main,
                    observed_at=_utc_now(),
                    requires_telegram_approval=True,
                    continuous_session=continuous_session,
                )
                prepared_path = _stage_telegram_candidate(
                    args.telegram_prepare_state_root,
                    preview,
                    authorization_id=str(runtime["authorization_id"]),
                )
                prepare_message = create_prepare_message(
                    preview,
                    runtime_authorization=runtime,
                    key=key,
                    key_id=args.transport_key_id,
                    created_at=_utc_now(),
                )
                prepare_message_id, prepare_attempts = (
                    _publish_control_with_bounded_retry(
                        publisher,
                        topic_path=topic_path,
                        payload=prepare_message,
                    )
                )
                print(
                    json.dumps(
                        {
                            "status": "fast_live_prepare_published",
                            "message_id": prepare_message_id,
                            "intent_id": prepare_message["intent_id"],
                            "prediction_id": prepare_message["prediction_id"],
                            "request_sha256": prepare_message["request_sha256"],
                            "prepared_path": str(prepared_path),
                            "risk_status": "pending",
                            "publish_attempts": prepare_attempts,
                            "network_submission_attempt_consumed": False,
                            "real_order_submitted": False,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

                preview_intent_id = str(preview["intent_id"])
                finalized = _load_telegram_state(
                    args.telegram_prepare_state_root,
                    preview_intent_id,
                    "finalized.json",
                )
                if finalized is None:
                    risk_started_at = _utc_now()
                    finalized = prepare_fast_live_candidate(
                        engine=engine,
                        activated_at=activated_at,
                        observed_at=risk_started_at,
                        interlock=interlock,
                        api_healthy=True,
                        official_open_order_count=args.official_open_order_count,
                        collateral_balance_usd=collateral,
                    )
                    risk_completed_at = _utc_now()
                    preview_created_at = datetime.fromisoformat(
                        str(preview["timing"]["prepared_observed_at"])
                    ).astimezone(UTC)
                    finalized["parallel_timing"] = {
                        "preview_created_at": preview_created_at.isoformat(),
                        "risk_started_at": risk_started_at.isoformat(),
                        "risk_completed_at": risk_completed_at.isoformat(),
                        "preview_to_risk_start_ms": (
                            risk_started_at - preview_created_at
                        ).total_seconds()
                        * 1000,
                        "risk_evaluation_ms": (
                            risk_completed_at - risk_started_at
                        ).total_seconds()
                        * 1000,
                        "preview_to_risk_complete_ms": (
                            risk_completed_at - preview_created_at
                        ).total_seconds()
                        * 1000,
                    }
                    finalized_status = str(finalized.get("status") or "")
                    if finalized_status != "prepared":
                        cancel = {
                            "status": "cancelled",
                            "reason": (
                                str(finalized.get("reason") or "")
                                or "live_risk_not_eligible"
                            ),
                            "preview_intent_id": preview_intent_id,
                            "prediction_id": str(
                                preview.get("prediction_id") or ""
                            ),
                            "request_sha256": str(
                                prepare_message["request_sha256"]
                            ),
                            "finalized_status": finalized_status,
                            "finalized": finalized,
                            "cancelled_at": _utc_now().isoformat(),
                        }
                        _write_telegram_state_once(
                            args.telegram_prepare_state_root,
                            preview_intent_id,
                            "cancel.json",
                            cancel,
                        )
                        _clear_staged_telegram_candidate(
                            args.telegram_prepare_state_root,
                            preview_intent_id,
                        )
                        print(
                            json.dumps(
                                {
                                    "status": "fast_live_preview_cancelled",
                                    **cancel,
                                },
                                sort_keys=True,
                                default=str,
                            ),
                            flush=True,
                        )
                        retryable = finalized.get("retryable") is True
                        time.sleep(
                            max(
                                args.poll_seconds,
                                1.0 if retryable else 0.05,
                            )
                        )
                        continue

                    preview_request_hash = fast_live_request_sha256(
                        preview
                    )
                    finalized_request_hash = fast_live_request_sha256(
                        finalized
                    )
                    if (
                        str(finalized["prediction_id"])
                        != str(preview["prediction_id"])
                        or str(finalized["paper_order_id"])
                        != str(preview["paper_order_id"])
                        or finalized_request_hash != preview_request_hash
                    ):
                        raise RuntimeError(
                            "fast live finalized risk candidate changed "
                            "the approved request"
                        )
                    _write_telegram_state_once(
                        args.telegram_prepare_state_root,
                        preview_intent_id,
                        "finalized.json",
                        finalized,
                    )

                if (
                    str(finalized.get("prediction_id") or "")
                    != str(preview.get("prediction_id") or "")
                    or str(finalized.get("paper_order_id") or "")
                    != str(preview.get("paper_order_id") or "")
                    or fast_live_request_sha256(finalized)
                    != fast_live_request_sha256(preview)
                ):
                    raise RuntimeError(
                        "fast live persisted finalized candidate "
                        "does not match preview"
                    )

                while True:
                    now = _utc_now()
                    market_end = datetime.fromisoformat(
                        str(finalized["market_end_at"])
                    ).astimezone(UTC)
                    if now >= runtime_expires_at:
                        closed = {
                            "status": "telegram_expired",
                            "intent_id": str(finalized["intent_id"]),
                            "prediction_id": str(
                                finalized["prediction_id"]
                            ),
                            "paper_order_id": str(
                                finalized["paper_order_id"]
                            ),
                            "request_sha256": str(
                                prepare_message["request_sha256"]
                            ),
                            "reason": "live_session_authorization_expired",
                            "network_submission_attempt_consumed": False,
                            "real_order_submitted": False,
                            "external_order_id": None,
                        }
                        recorded = record_fast_live_result(
                            engine=engine,
                            result=closed,
                            observed_at=now,
                        )
                        _clear_staged_telegram_candidate(
                            args.telegram_prepare_state_root,
                            preview_intent_id,
                        )
                        print(
                            json.dumps(
                                {
                                    **closed,
                                    "recorded": recorded,
                                },
                                sort_keys=True,
                                default=str,
                            ),
                            flush=True,
                        )
                        break
                    approval_path = _approval_record_path(
                        args.telegram_approval_state_root,
                        preview_intent_id,
                    )
                    if approval_path.is_file():
                        approval = _load_json(approval_path)
                        approval_status = str(approval.get("status") or "")
                        if approval_status == "approved":
                            state_now = _load_state(args.project_state)
                            runtime_now = load_private_json(
                                args.runtime_authorization,
                                label="fast live runtime authorization",
                            )
                            verify_runtime_authorization(
                                runtime_now,
                                state=state_now,
                                expected_main=args.expected_main,
                                observed_at=now,
                                requires_telegram_approval=True,
                                continuous_session=continuous_session,
                            )
                            approval_message = create_approval_message(
                                finalized,
                                approval=approval,
                                approval_prepared=preview,
                                runtime_authorization=runtime_now,
                                key=key,
                                key_id=args.transport_key_id,
                                created_at=now,
                            )
                            parallel_timing = finalized.get("parallel_timing")
                            approval_vs_risk_ms = None
                            if isinstance(parallel_timing, dict):
                                risk_completed_raw = str(
                                    parallel_timing.get("risk_completed_at")
                                    or ""
                                )
                                if risk_completed_raw:
                                    risk_completed_at = datetime.fromisoformat(
                                        risk_completed_raw
                                    ).astimezone(UTC)
                                    human_approved_at = datetime.fromisoformat(
                                        str(approval["approved_at"])
                                    ).astimezone(UTC)
                                    approval_vs_risk_ms = (
                                        human_approved_at - risk_completed_at
                                    ).total_seconds() * 1000
                            approval_published_at = _utc_now()
                            approval_result_deadline = (
                                approval_published_at
                                + timedelta(
                                    seconds=float(
                                        FAST_LIVE_RESULT_STALL_SECONDS
                                    )
                                )
                            )
                            with result_state_lock:
                                result_state["awaited_intent_id"] = str(
                                    approval_message["intent_id"]
                                )
                                result_state["awaited_request_sha256"] = str(
                                    approval_message["request_sha256"]
                                )
                                result_state["awaited_result_deadline"] = (
                                    approval_result_deadline
                                )
                                result_state["result_stall_reported"] = False
                            message_id, publish_attempts = (
                                _publish_control_with_bounded_retry(
                                    publisher,
                                    topic_path=topic_path,
                                    payload=approval_message,
                                )
                            )
                            receipt_path = _receipt_path(
                                args.receipt_dir,
                                str(approval_message["intent_id"]),
                                str(approval_message["request_sha256"]),
                            )
                            if not receipt_path.exists():
                                _write_receipt(
                                    receipt_path,
                                    {
                                        "status": "fast_live_approval_published",
                                        "message_id": message_id,
                                        "intent_id": approval_message["intent_id"],
                                        "approval_candidate_id": (
                                            approval_message[
                                                "approval_candidate_id"
                                            ]
                                        ),
                                        "request_sha256": approval_message["request_sha256"],
                                        "prepared_sha256": approval_message["prepared_sha256"],
                                        "prepare_sha256": approval_message["prepare_sha256"],
                                        "authorization_id": approval_message["authorization_id"],
                                        "published_at": approval_published_at.isoformat(),
                                        "result_wait_deadline": (
                                            approval_result_deadline.isoformat()
                                        ),
                                        "publish_attempts": publish_attempts,
                                        "parallel_timing": parallel_timing,
                                        "approval_vs_risk_ms": approval_vs_risk_ms,
                                        "network_submission_attempt_consumed": False,
                                        "real_order_submitted": False,
                                    },
                                )
                            print(
                                json.dumps(
                                    {
                                        "status": "fast_live_approval_published",
                                        "intent_id": approval_message["intent_id"],
                                        "approval_candidate_id": (
                                            approval_message[
                                                "approval_candidate_id"
                                            ]
                                        ),
                                        "request_sha256": approval_message["request_sha256"],
                                        "message_id": message_id,
                                        "parallel_timing": parallel_timing,
                                        "approval_vs_risk_ms": approval_vs_risk_ms,
                                        "network_submission_attempt_consumed": False,
                                        "real_order_submitted": False,
                                    },
                                    sort_keys=True,
                                ),
                                flush=True,
                            )
                            waiting_for_result = True
                            break
                        if approval_status in {"skipped", "expired"}:
                            closed = {
                                "status": (
                                    "telegram_skipped"
                                    if approval_status == "skipped"
                                    else "telegram_expired"
                                ),
                                "intent_id": str(finalized["intent_id"]),
                                "prediction_id": str(
                                    finalized["prediction_id"]
                                ),
                                "paper_order_id": str(
                                    finalized["paper_order_id"]
                                ),
                                "request_sha256": str(
                                    prepare_message["request_sha256"]
                                ),
                                "network_submission_attempt_consumed": False,
                                "real_order_submitted": False,
                                "external_order_id": None,
                            }
                            recorded = record_fast_live_result(
                                engine=engine,
                                result=closed,
                                observed_at=now,
                            )
                            _clear_staged_telegram_candidate(
                                args.telegram_prepare_state_root,
                                preview_intent_id,
                            )
                            print(
                                json.dumps(
                                    {
                                        **closed,
                                        "recorded": recorded,
                                    },
                                    sort_keys=True,
                                    default=str,
                                ),
                                flush=True,
                            )
                            break
                    if (market_end - now).total_seconds() <= 10:
                        closed = {
                            "status": "telegram_expired",
                            "intent_id": str(finalized["intent_id"]),
                            "prediction_id": str(finalized["prediction_id"]),
                            "paper_order_id": str(finalized["paper_order_id"]),
                            "request_sha256": str(
                                prepare_message["request_sha256"]
                            ),
                            "network_submission_attempt_consumed": False,
                            "real_order_submitted": False,
                            "external_order_id": None,
                        }
                        recorded = record_fast_live_result(
                            engine=engine,
                            result=closed,
                            observed_at=now,
                        )
                        _clear_staged_telegram_candidate(
                            args.telegram_prepare_state_root,
                            preview_intent_id,
                        )
                        print(
                            json.dumps(
                                {
                                    **closed,
                                    "recorded": recorded,
                                },
                                sort_keys=True,
                                default=str,
                            ),
                            flush=True,
                        )
                        break
                    time.sleep(max(args.poll_seconds, 0.05))
                continue

            report = prepare_fast_live_candidate(
                engine=engine,
                activated_at=activated_at,
                observed_at=observed,
                interlock=interlock,
                api_healthy=True,
                official_open_order_count=args.official_open_order_count,
                collateral_balance_usd=collateral,
            )
            status = str(report.get("status") or "")
            if status == "waiting":
                time.sleep(args.poll_seconds)
                continue
            if status in {"skipped", "blocked"}:
                print(
                    json.dumps(
                        report,
                        sort_keys=True,
                        default=str,
                    ),
                    flush=True,
                )
                time.sleep(args.poll_seconds)
                continue
            if status != "prepared":
                print(
                    json.dumps(
                        report,
                        sort_keys=True,
                        default=str,
                    ),
                    flush=True,
                )
                return 2

            state = _load_state(args.project_state)
            runtime = load_private_json(
                args.runtime_authorization,
                label="fast live runtime authorization",
            )
            verify_runtime_authorization(
                runtime,
                state=state,
                expected_main=args.expected_main,
                observed_at=_utc_now(),
                requires_telegram_approval=False,
                continuous_session=continuous_session,
            )
            envelope = create_envelope(
                report,
                runtime_authorization=runtime,
                key=key,
                key_id=args.transport_key_id,
                created_at=_utc_now(),
            )
            receipt_path = _receipt_path(
                args.receipt_dir,
                str(envelope["intent_id"]),
                str(envelope["request_sha256"]),
            )
            if receipt_path.exists():
                print(
                    json.dumps(
                        {
                            "status": "already_published",
                            "intent_id": envelope["intent_id"],
                            "request_sha256": envelope["request_sha256"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if not continuous_session:
                    return 0
                time.sleep(args.poll_seconds)
                continue

            direct_published_at = _utc_now()
            direct_result_deadline = direct_published_at + timedelta(
                seconds=float(FAST_LIVE_RESULT_MAX_AGE_SECONDS) + 5.0
            )
            with result_state_lock:
                result_state["awaited_intent_id"] = str(envelope["intent_id"])
                result_state["awaited_request_sha256"] = str(
                    envelope["request_sha256"]
                )
                result_state["awaited_result_deadline"] = (
                    direct_result_deadline
                )
                result_state["result_stall_reported"] = False
            publish_started = time.monotonic_ns()
            message_id, publish_attempts = _publish_with_bounded_retry(
                publisher,
                topic_path=topic_path,
                envelope=envelope,
            )
            publish_completed = time.monotonic_ns()
            receipt = {
                "status": "fast_live_published",
                "message_id": message_id,
                "intent_id": envelope["intent_id"],
                "request_sha256": envelope["request_sha256"],
                "prepared_sha256": envelope["prepared_sha256"],
                "authorization_id": envelope["authorization_id"],
                "published_at": direct_published_at.isoformat(),
                "result_wait_deadline": direct_result_deadline.isoformat(),
                "publish_latency_ms": (
                    publish_completed - publish_started
                ) / 1_000_000,
                "publish_attempts": publish_attempts,
                "network_submission_attempt_consumed": False,
                "real_order_submitted": False,
            }
            _write_receipt(receipt_path, receipt)
            print(json.dumps(receipt, sort_keys=True), flush=True)
            waiting_for_result = True
    finally:
        result_future.cancel()
        result_subscriber.close()
        engine.dispose()
        publisher.stop()


if __name__ == "__main__":
    raise SystemExit(main())
