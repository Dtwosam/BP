from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.live_readiness.geoblock import GeoblockClient, GeoblockError
from bp_engine.live_readiness.interlock import (
    ActivationManifestError,
    load_activation_manifest,
)
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.service import (
    LiveReadinessService,
    OfficialOrderSnapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bp-live-readiness",
        description="Read-only Phase 14 readiness diagnostics.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    report = subcommands.add_parser("report", help="Show stored readiness evidence.")
    report.add_argument("--database-url")

    manifest = subcommands.add_parser(
        "validate-activation-manifest",
        help="Validate activation evidence without changing runtime state.",
    )
    manifest.add_argument("--path", required=True)
    manifest.add_argument("--expected-head", required=True)

    subcommands.add_parser("geoblock", help="Check the configured public geoblock endpoint.")

    reconcile = subcommands.add_parser(
        "reconcile-fixture",
        help="Validate a deterministic official-order fixture against the local ledger.",
    )
    reconcile.add_argument("--fixture", required=True)
    reconcile.add_argument("--database-url")
    return parser


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _write_json(payload: dict[str, Any], *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(json.dumps(_json_value(payload), sort_keys=True, separators=(",", ":")))
    stream.write("\n")


def _settings(database_url: str | None = None) -> Settings:
    settings = Settings(_env_file=None)
    if database_url:
        return settings.model_copy(update={"database_url": database_url})
    return settings


def _service(*, settings: Settings) -> LiveReadinessService:
    engine = create_engine(settings.database_url)
    return LiveReadinessService(
        engine=engine,
        repository=LiveReadinessRepository(),
        settings=settings,
        activation_loader=lambda **_kwargs: None,  # type: ignore[arg-type,return-value]
        kill_switch_probe=lambda _path: True,
        geoblock_check=lambda **_kwargs: None,  # type: ignore[arg-type,return-value]
        sdk_health=lambda: False,
        wallet_configured=lambda: False,
    )


def _report(database_url: str | None) -> int:
    service = _service(settings=_settings(database_url))
    _write_json(service.get_report())
    return 0


def _validate_activation_manifest(path: str, expected_head: str) -> int:
    try:
        manifest = load_activation_manifest(
            path,
            expected_git_sha=expected_head,
            observed_at=datetime.now(UTC),
        )
    except ActivationManifestError:
        _write_json({"error": "activation_manifest_invalid", "ok": False}, error=True)
        return 2

    _write_json(
        {
            "authorization_id": manifest.authorization_id,
            "expires_at": manifest.expires_at,
            "git_sha": manifest.git_sha,
            "issued_at": manifest.issued_at,
            "ok": True,
        }
    )
    return 0


def _geoblock() -> int:
    settings = _settings()
    try:
        result = GeoblockClient(url=settings.polymarket_geoblock_url).check()
    except GeoblockError:
        _write_json({"error": "geoblock_error", "ok": False}, error=True)
        return 2

    _write_json(
        {
            "blocked": result.blocked,
            "checked_at": result.checked_at,
            "country": result.country,
            "ok": True,
            "region": result.region,
        }
    )
    return 0


def _official_order(payload: dict[str, Any], observed_at: datetime) -> OfficialOrderSnapshot:
    order_observed_at = payload.get("observed_at")
    if order_observed_at is None:
        normalized_observed_at = observed_at
    else:
        normalized_observed_at = datetime.fromisoformat(str(order_observed_at))
    average_fill_price = payload.get("average_fill_price")
    return OfficialOrderSnapshot(
        external_order_id=str(payload["external_order_id"]),
        token_id=str(payload["token_id"]),
        side=str(payload["side"]),
        status=str(payload["status"]),
        original_size=Decimal(str(payload["original_size"])),
        filled_size=Decimal(str(payload["filled_size"])),
        limit_price=Decimal(str(payload["limit_price"])),
        average_fill_price=(
            None if average_fill_price is None else Decimal(str(average_fill_price))
        ),
        observed_at=normalized_observed_at,
    )


def _reconcile_fixture(fixture_path: str, database_url: str | None) -> int:
    try:
        payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("fixture must be an object")
        observed_at = datetime.fromisoformat(str(payload["observed_at"]))
        raw_orders = payload["official_orders"]
        if not isinstance(raw_orders, list):
            raise ValueError("official_orders must be a list")
        official_orders = tuple(
            _official_order(order, observed_at)
            for order in raw_orders
            if isinstance(order, dict)
        )
        if len(official_orders) != len(raw_orders):
            raise ValueError("official order must be an object")
        result = _service(settings=_settings(database_url)).reconcile_snapshot(
            official_orders=official_orders,
            observed_at=observed_at,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        _write_json({"error": "reconciliation_fixture_invalid", "ok": False}, error=True)
        return 2

    _write_json(
        {
            "critical_discrepancy_count": result.critical_discrepancy_count,
            "issues": [issue.as_mapping() for issue in result.issues],
            "observed_at": result.observed_at,
            "unresolved_count": result.unresolved_count,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        return _report(args.database_url)
    if args.command == "validate-activation-manifest":
        return _validate_activation_manifest(args.path, args.expected_head)
    if args.command == "geoblock":
        return _geoblock()
    if args.command == "reconcile-fixture":
        return _reconcile_fixture(args.fixture, args.database_url)
    raise RuntimeError("unreachable command")
