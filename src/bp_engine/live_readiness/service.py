from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine

from bp_engine.config import Settings, TradingMode
from bp_engine.live_readiness.geoblock import GeoblockError
from bp_engine.live_readiness.interlock import ActivationManifestError
from bp_engine.live_readiness.models import ActivationManifest, GeoblockResult
from bp_engine.live_readiness.repository import LiveReadinessRepository, LiveStoreResult
from bp_engine.storage import schema

_KNOWN_EXTERNAL_STATUSES = frozenset(
    {
        "open",
        "live",
        "partially_filled",
        "filled",
        "matched",
        "cancelled",
        "canceled",
        "expired",
        "rejected",
    }
)
_TERMINAL_CANCELLATION_STATUSES = frozenset({"cancelled", "canceled"})
_HEX = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class ReadinessCheck:
    candidate_git_sha: str
    observed_at: datetime
    eligible: bool
    reasons: tuple[str, ...]
    evidence: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_git_sha",
            _sha(self.candidate_git_sha, "candidate_git_sha"),
        )
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "reasons", _reason_tuple(self.reasons))
        object.__setattr__(self, "evidence", dict(self.evidence))
        if self.eligible and self.reasons:
            raise ValueError("eligible readiness check cannot contain blocking reasons")


@dataclass(frozen=True)
class OfficialOrderSnapshot:
    external_order_id: str
    token_id: str
    side: str
    status: str
    original_size: Decimal
    filled_size: Decimal
    limit_price: Decimal
    average_fill_price: Decimal | None
    observed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "external_order_id",
            _text(self.external_order_id, "external_order_id"),
        )
        object.__setattr__(self, "token_id", _text(self.token_id, "token_id"))
        side = _text(self.side, "side").upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "status", _text(self.status, "status").lower())

        original_size = _decimal(self.original_size, "original_size")
        filled_size = _decimal(self.filled_size, "filled_size")
        limit_price = _decimal(self.limit_price, "limit_price")
        if original_size <= 0:
            raise ValueError("original_size must be positive")
        if filled_size < 0:
            raise ValueError("filled_size must be nonnegative")
        if limit_price <= 0 or limit_price > 1:
            raise ValueError("limit_price must be within (0, 1]")
        object.__setattr__(self, "original_size", original_size)
        object.__setattr__(self, "filled_size", filled_size)
        object.__setattr__(self, "limit_price", limit_price)

        if self.average_fill_price is not None:
            average_fill_price = _decimal(
                self.average_fill_price,
                "average_fill_price",
            )
            if average_fill_price <= 0 or average_fill_price > 1:
                raise ValueError("average_fill_price must be within (0, 1]")
            object.__setattr__(self, "average_fill_price", average_fill_price)
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))


@dataclass(frozen=True)
class ReconciliationIssue:
    code: str
    critical: bool
    intent_id: str | None = None
    external_order_id: str | None = None
    evidence: dict[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _text(self.code, "code"))
        if self.intent_id is not None:
            object.__setattr__(self, "intent_id", _text(self.intent_id, "intent_id"))
        if self.external_order_id is not None:
            object.__setattr__(
                self,
                "external_order_id",
                _text(self.external_order_id, "external_order_id"),
            )
        object.__setattr__(self, "evidence", dict(self.evidence or {}))

    def as_mapping(self) -> dict[str, object]:
        return {
            "code": self.code,
            "critical": self.critical,
            "intent_id": self.intent_id,
            "external_order_id": self.external_order_id,
            "evidence": dict(self.evidence or {}),
        }


@dataclass(frozen=True)
class ReconciliationResult:
    observed_at: datetime
    unresolved_count: int
    critical_discrepancy_count: int
    issues: tuple[ReconciliationIssue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "issues", tuple(self.issues))
        if self.unresolved_count < 0:
            raise ValueError("unresolved_count must be nonnegative")
        if (
            self.critical_discrepancy_count < 0
            or self.critical_discrepancy_count > self.unresolved_count
        ):
            raise ValueError("critical_discrepancy_count must be within unresolved count")
        if self.unresolved_count != len(self.issues):
            raise ValueError("unresolved_count must equal issue count")
        critical_count = sum(1 for issue in self.issues if issue.critical)
        if critical_count != self.critical_discrepancy_count:
            raise ValueError("critical_discrepancy_count must equal critical issue count")


class LiveReadinessService:
    """Read-only live-readiness and reconciliation orchestration.

    The service never constructs a secure trading client. Callers inject only
    read-only probes and normalized official order snapshots.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        repository: LiveReadinessRepository,
        settings: Settings,
        activation_loader: Callable[..., ActivationManifest],
        kill_switch_probe: Callable[[str], bool],
        geoblock_check: Callable[..., GeoblockResult],
        sdk_health: Callable[[], bool],
        wallet_configured: Callable[[], bool],
        reconciliation_stale_after_seconds: float = 30.0,
    ) -> None:
        if reconciliation_stale_after_seconds < 0:
            raise ValueError("reconciliation_stale_after_seconds must be nonnegative")
        self._engine = engine
        self._repository = repository
        self._settings = settings
        self._activation_loader = activation_loader
        self._kill_switch_probe = kill_switch_probe
        self._geoblock_check = geoblock_check
        self._sdk_health = sdk_health
        self._wallet_configured = wallet_configured
        self._reconciliation_stale_after_seconds = Decimal(
            str(reconciliation_stale_after_seconds)
        )

    def build_readiness_check(
        self,
        *,
        expected_git_sha: str,
        observed_at: datetime,
    ) -> ReadinessCheck:
        expected_sha = _sha(expected_git_sha, "expected_git_sha")
        observed = _utc(observed_at, "observed_at")
        reasons: list[str] = []

        mode = self._settings.mode
        mode_value = mode.value if isinstance(mode, TradingMode) else str(mode)
        if mode != TradingMode.LIVE:
            _append_reason(reasons, "mode_not_live")
        if not self._settings.live_trading_enabled:
            _append_reason(reasons, "live_trading_disabled")

        limits = {
            "max_trade_size_usd": _setting_decimal(self._settings.max_trade_size_usd),
            "max_total_exposure_usd": _setting_decimal(
                self._settings.max_total_exposure_usd
            ),
            "max_daily_loss_usd": _setting_decimal(self._settings.max_daily_loss_usd),
            "max_consecutive_losses": self._settings.max_consecutive_losses,
        }
        if limits["max_trade_size_usd"] <= 0:
            _append_reason(reasons, "trade_size_limit_zero")
        if limits["max_total_exposure_usd"] <= 0:
            _append_reason(reasons, "total_exposure_limit_zero")
        if limits["max_daily_loss_usd"] <= 0:
            _append_reason(reasons, "daily_loss_limit_zero")
        if int(limits["max_consecutive_losses"]) <= 0:
            _append_reason(reasons, "consecutive_loss_limit_zero")

        activation_evidence = self._activation_evidence(
            expected_git_sha=expected_sha,
            observed_at=observed,
            reasons=reasons,
        )
        kill_switch_evidence = self._kill_switch_evidence(reasons=reasons)
        geoblock_evidence = self._geoblock_evidence(
            observed_at=observed,
            reasons=reasons,
        )

        sdk_healthy = _safe_boolean_probe(self._sdk_health)
        if not sdk_healthy:
            _append_reason(reasons, "sdk_unhealthy")
        wallet_configured = _safe_boolean_probe(self._wallet_configured)
        if not wallet_configured:
            _append_reason(reasons, "wallet_not_configured")

        with self._engine.begin() as connection:
            reconciliation_evidence = _latest_reconciliation_evidence(
                connection,
                observed_at=observed,
            )
        if reconciliation_evidence["status"] == "unavailable":
            _append_reason(reasons, "reconciliation_unavailable")
        elif int(reconciliation_evidence["critical_count"]) > 0:
            _append_reason(reasons, "reconciliation_blocked")

        evidence: dict[str, object] = {
            "mode": mode_value,
            "live_trading_enabled": bool(self._settings.live_trading_enabled),
            "limits": {
                "max_trade_size_usd": str(limits["max_trade_size_usd"]),
                "max_total_exposure_usd": str(limits["max_total_exposure_usd"]),
                "max_daily_loss_usd": str(limits["max_daily_loss_usd"]),
                "max_consecutive_losses": int(limits["max_consecutive_losses"]),
            },
            "activation": activation_evidence,
            "kill_switch": kill_switch_evidence,
            "geoblock": geoblock_evidence,
            "sdk_healthy": sdk_healthy,
            "wallet_configured": wallet_configured,
            "reconciliation": reconciliation_evidence,
        }
        return ReadinessCheck(
            candidate_git_sha=expected_sha,
            observed_at=observed,
            eligible=not reasons,
            reasons=tuple(reasons),
            evidence=evidence,
        )

    def store_readiness_check(self, check: ReadinessCheck) -> LiveStoreResult:
        if not isinstance(check, ReadinessCheck):
            raise TypeError("check must be a ReadinessCheck")
        with self._engine.begin() as connection:
            return self._repository.store_readiness_check(
                connection,
                candidate_git_sha=check.candidate_git_sha,
                observed_at=check.observed_at,
                eligible=check.eligible,
                reasons=check.reasons,
                evidence=check.evidence,
            )

    def reconcile_snapshot(
        self,
        *,
        official_orders: Sequence[OfficialOrderSnapshot],
        observed_at: datetime,
    ) -> ReconciliationResult:
        observed = _utc(observed_at, "observed_at")
        snapshots = tuple(official_orders)
        if any(not isinstance(order, OfficialOrderSnapshot) for order in snapshots):
            raise TypeError("official_orders must contain OfficialOrderSnapshot values")

        with self._engine.begin() as connection:
            intents = connection.execute(
                select(schema.live_order_intents).order_by(schema.live_order_intents.c.id)
            ).mappings().all()
            events = connection.execute(
                select(schema.live_order_events).order_by(schema.live_order_events.c.id)
            ).mappings().all()
            risk_rows = connection.execute(
                select(schema.live_risk_decisions)
            ).mappings().all()

            issues = _reconciliation_issues(
                intents=intents,
                events=events,
                risk_rows=risk_rows,
                official_orders=snapshots,
                observed_at=observed,
                stale_after_seconds=self._reconciliation_stale_after_seconds,
            )
            sorted_issues = tuple(sorted(issues, key=_issue_sort_key))
            result = ReconciliationResult(
                observed_at=observed,
                unresolved_count=len(sorted_issues),
                critical_discrepancy_count=sum(
                    1 for issue in sorted_issues if issue.critical
                ),
                issues=sorted_issues,
            )
            self._repository.store_reconciliation_run(
                connection,
                observed_at=observed,
                unresolved_count=result.unresolved_count,
                critical_count=result.critical_discrepancy_count,
                evidence={
                    "local_intent_count": len(intents),
                    "local_event_count": len(events),
                    "official_order_count": len(snapshots),
                    "issues": [issue.as_mapping() for issue in result.issues],
                },
            )
        return result

    def get_report(self) -> dict[str, object]:
        with self._engine.begin() as connection:
            readiness = connection.execute(
                select(schema.live_readiness_checks)
                .order_by(
                    schema.live_readiness_checks.c.observed_at.desc(),
                    schema.live_readiness_checks.c.id.desc(),
                )
                .limit(1)
            ).mappings().one_or_none()
            reconciliation = connection.execute(
                select(schema.live_reconciliation_runs)
                .order_by(
                    schema.live_reconciliation_runs.c.observed_at.desc(),
                    schema.live_reconciliation_runs.c.id.desc(),
                )
                .limit(1)
            ).mappings().one_or_none()

        if readiness is None:
            return {
                "eligible": False,
                "reasons": ("readiness_unavailable",),
                "candidate_git_sha": None,
                "observed_at": None,
                "mode": None,
                "live_trading_enabled": False,
                "activation_authorized": False,
                "kill_switch_engaged": True,
                "geoblock_blocked": None,
                "country": None,
                "region": None,
                "sdk_healthy": False,
                "wallet_configured": False,
                "reconciliation_status": "unavailable",
                "critical_discrepancy_count": None,
            }

        evidence = dict(readiness["evidence"] or {})
        activation = _mapping(evidence.get("activation"))
        kill_switch = _mapping(evidence.get("kill_switch"))
        geoblock = _mapping(evidence.get("geoblock"))
        reconciliation_evidence = _mapping(evidence.get("reconciliation"))
        critical_count: int | None
        if reconciliation is None:
            critical_count = _optional_int(reconciliation_evidence.get("critical_count"))
        else:
            critical_count = int(reconciliation["critical_count"])
        return {
            "eligible": bool(readiness["eligible"]),
            "reasons": tuple(readiness["reasons"] or ()),
            "candidate_git_sha": str(readiness["candidate_git_sha"]),
            "observed_at": _utc(readiness["observed_at"], "readiness.observed_at").isoformat(),
            "mode": evidence.get("mode"),
            "live_trading_enabled": bool(evidence.get("live_trading_enabled", False)),
            "activation_authorized": bool(activation.get("authorized", False)),
            "kill_switch_engaged": bool(kill_switch.get("engaged", True)),
            "geoblock_blocked": geoblock.get("blocked"),
            "country": geoblock.get("country"),
            "region": geoblock.get("region"),
            "sdk_healthy": bool(evidence.get("sdk_healthy", False)),
            "wallet_configured": bool(evidence.get("wallet_configured", False)),
            "reconciliation_status": reconciliation_evidence.get(
                "status",
                "unavailable",
            ),
            "critical_discrepancy_count": critical_count,
        }

    def _activation_evidence(
        self,
        *,
        expected_git_sha: str,
        observed_at: datetime,
        reasons: list[str],
    ) -> dict[str, object]:
        try:
            manifest = self._activation_loader(
                self._settings.live_activation_manifest_path,
                expected_git_sha=expected_git_sha,
                observed_at=observed_at,
            )
        except (ActivationManifestError, OSError, ValueError, TypeError):
            _append_reason(reasons, "activation_manifest_invalid")
            return {
                "status": "invalid",
                "authorized": False,
                "authorization_id": None,
                "git_sha": None,
                "issued_at": None,
                "expires_at": None,
            }
        except Exception:
            _append_reason(reasons, "activation_manifest_invalid")
            return {
                "status": "invalid",
                "authorized": False,
                "authorization_id": None,
                "git_sha": None,
                "issued_at": None,
                "expires_at": None,
            }

        if not isinstance(manifest, ActivationManifest):
            _append_reason(reasons, "activation_manifest_invalid")
            return {
                "status": "invalid",
                "authorized": False,
                "authorization_id": None,
                "git_sha": None,
                "issued_at": None,
                "expires_at": None,
            }
        if not manifest.authorized:
            _append_reason(reasons, "activation_not_authorized")
        return {
            "status": "ok",
            "authorized": bool(manifest.authorized),
            "authorization_id": manifest.authorization_id,
            "git_sha": manifest.git_sha,
            "issued_at": manifest.issued_at.isoformat(),
            "expires_at": manifest.expires_at.isoformat(),
        }

    def _kill_switch_evidence(self, *, reasons: list[str]) -> dict[str, object]:
        try:
            engaged = self._kill_switch_probe(self._settings.live_kill_switch_path) is True
            status = "ok"
        except Exception:
            engaged = True
            status = "error"
        if engaged:
            _append_reason(reasons, "kill_switch_engaged")
        return {"status": status, "engaged": engaged}

    def _geoblock_evidence(
        self,
        *,
        observed_at: datetime,
        reasons: list[str],
    ) -> dict[str, object]:
        try:
            result = self._geoblock_check(observed_at=observed_at)
        except (GeoblockError, OSError, ValueError, TypeError):
            _append_reason(reasons, "geoblock_error")
            return {
                "status": "error",
                "blocked": None,
                "country": None,
                "region": None,
            }
        except Exception:
            _append_reason(reasons, "geoblock_error")
            return {
                "status": "error",
                "blocked": None,
                "country": None,
                "region": None,
            }
        if not isinstance(result, GeoblockResult):
            _append_reason(reasons, "geoblock_error")
            return {
                "status": "error",
                "blocked": None,
                "country": None,
                "region": None,
            }
        if result.blocked:
            _append_reason(reasons, "geographic_eligibility_blocked")
        return {
            "status": "ok",
            "blocked": bool(result.blocked),
            "country": result.country,
            "region": result.region,
        }


def _reconciliation_issues(
    *,
    intents: Sequence[Any],
    events: Sequence[Any],
    risk_rows: Sequence[Any],
    official_orders: Sequence[OfficialOrderSnapshot],
    observed_at: datetime,
    stale_after_seconds: Decimal,
) -> list[ReconciliationIssue]:
    issues: list[ReconciliationIssue] = []
    events_by_intent: dict[str, list[Any]] = defaultdict(list)
    for event in events:
        events_by_intent[str(event["intent_id"])].append(event)

    risk_by_id = {str(row["decision_id"]): row for row in risk_rows}
    local_external_to_intent: dict[str, Any] = {}
    duplicate_local_external_ids: set[str] = set()

    for intent in intents:
        intent_id = str(intent["intent_id"])
        intent_events = events_by_intent.get(intent_id, [])
        external_ids = {
            str(event["external_order_id"])
            for event in intent_events
            if event["external_order_id"]
        }
        rejected = any(str(event["event_type"]) == "rejected" for event in intent_events)
        if not rejected and not external_ids:
            issues.append(
                ReconciliationIssue(
                    code="intent_without_external_result",
                    critical=True,
                    intent_id=intent_id,
                    evidence={"prediction_id": str(intent["prediction_id"])},
                )
            )

        for external_order_id in external_ids:
            existing = local_external_to_intent.get(external_order_id)
            if existing is not None and str(existing["intent_id"]) != intent_id:
                duplicate_local_external_ids.add(external_order_id)
            else:
                local_external_to_intent[external_order_id] = intent

        risk = risk_by_id.get(str(intent["risk_decision_id"]))
        if risk is not None and risk["eligible"] is not True and external_ids:
            issues.append(
                ReconciliationIssue(
                    code="order_created_while_risk_blocked",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=sorted(external_ids)[0],
                    evidence={"risk_decision_id": str(intent["risk_decision_id"])},
                )
            )

    for external_order_id in sorted(duplicate_local_external_ids):
        issues.append(
            ReconciliationIssue(
                code="duplicate_external_order_id",
                critical=True,
                external_order_id=external_order_id,
                evidence={"source": "local_events"},
            )
        )

    official_counts = Counter(order.external_order_id for order in official_orders)
    for external_order_id, count in sorted(official_counts.items()):
        if count > 1:
            issues.append(
                ReconciliationIssue(
                    code="duplicate_external_order_id",
                    critical=True,
                    external_order_id=external_order_id,
                    evidence={"source": "official_snapshot", "count": count},
                )
            )

    first_official: dict[str, OfficialOrderSnapshot] = {}
    for order in official_orders:
        first_official.setdefault(order.external_order_id, order)

    for external_order_id, order in sorted(first_official.items()):
        intent = local_external_to_intent.get(external_order_id)
        if intent is None:
            issues.append(
                ReconciliationIssue(
                    code="external_order_without_local_intent",
                    critical=True,
                    external_order_id=external_order_id,
                    evidence={"status": order.status},
                )
            )
            continue

        intent_id = str(intent["intent_id"])
        requested_size = _decimal(intent["size"], "intent.size")
        local_limit = _decimal(intent["limit_price"], "intent.limit_price")
        local_token = str(intent["token_id"])
        local_side = str(intent["side"]).upper()

        if order.token_id != local_token:
            issues.append(
                ReconciliationIssue(
                    code="external_token_mismatch",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={"expected_token_id": local_token},
                )
            )
        if order.side != local_side:
            issues.append(
                ReconciliationIssue(
                    code="external_side_mismatch",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={"expected_side": local_side},
                )
            )
        if order.original_size != requested_size:
            issues.append(
                ReconciliationIssue(
                    code="external_order_size_mismatch",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={
                        "requested_size": str(requested_size),
                        "official_original_size": str(order.original_size),
                    },
                )
            )
        if order.limit_price != local_limit:
            issues.append(
                ReconciliationIssue(
                    code="external_limit_price_mismatch",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={
                        "local_limit_price": str(local_limit),
                        "official_limit_price": str(order.limit_price),
                    },
                )
            )
        if order.filled_size > requested_size:
            issues.append(
                ReconciliationIssue(
                    code="filled_amount_exceeds_request",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={
                        "requested_size": str(requested_size),
                        "filled_size": str(order.filled_size),
                    },
                )
            )
        if (
            local_side == "BUY"
            and order.average_fill_price is not None
            and order.average_fill_price > local_limit
        ):
            issues.append(
                ReconciliationIssue(
                    code="fill_price_above_limit",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={
                        "limit_price": str(local_limit),
                        "average_fill_price": str(order.average_fill_price),
                    },
                )
            )

        if order.status not in _KNOWN_EXTERNAL_STATUSES:
            issues.append(
                ReconciliationIssue(
                    code="unknown_external_state",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={"status": order.status},
                )
            )
        age_seconds = Decimal(str((observed_at - order.observed_at).total_seconds()))
        if age_seconds < 0:
            issues.append(
                ReconciliationIssue(
                    code="external_state_from_future",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={},
                )
            )
        elif age_seconds > stale_after_seconds:
            issues.append(
                ReconciliationIssue(
                    code="stale_external_state",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={"age_seconds": str(age_seconds)},
                )
            )

        local_cancelled = any(
            str(event["event_type"]) == "cancelled"
            for event in events_by_intent.get(intent_id, [])
        )
        if local_cancelled and order.status not in _TERMINAL_CANCELLATION_STATUSES:
            issues.append(
                ReconciliationIssue(
                    code="cancellation_disagreement",
                    critical=True,
                    intent_id=intent_id,
                    external_order_id=external_order_id,
                    evidence={"official_status": order.status},
                )
            )

    return _deduplicate_issues(issues)


def _deduplicate_issues(issues: Sequence[ReconciliationIssue]) -> list[ReconciliationIssue]:
    unique: dict[tuple[str, str, str], ReconciliationIssue] = {}
    for issue in issues:
        key = (
            issue.code,
            issue.external_order_id or "",
            issue.intent_id or "",
        )
        unique.setdefault(key, issue)
    return list(unique.values())


def _issue_sort_key(issue: ReconciliationIssue) -> tuple[str, str, str]:
    return (
        issue.code,
        issue.external_order_id or "",
        issue.intent_id or "",
    )


def _latest_reconciliation_evidence(
    connection: Connection,
    *,
    observed_at: datetime,
) -> dict[str, object]:
    row = connection.execute(
        select(schema.live_reconciliation_runs)
        .where(schema.live_reconciliation_runs.c.observed_at <= observed_at)
        .order_by(
            schema.live_reconciliation_runs.c.observed_at.desc(),
            schema.live_reconciliation_runs.c.id.desc(),
        )
        .limit(1)
    ).mappings().one_or_none()
    if row is None:
        return {
            "status": "unavailable",
            "reconciliation_id": None,
            "observed_at": None,
            "unresolved_count": 0,
            "critical_count": 0,
        }
    critical_count = int(row["critical_count"])
    return {
        "status": "blocked" if critical_count > 0 else "ok",
        "reconciliation_id": str(row["reconciliation_id"]),
        "observed_at": _utc(row["observed_at"], "reconciliation.observed_at").isoformat(),
        "unresolved_count": int(row["unresolved_count"]),
        "critical_count": critical_count,
    }


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _reason_tuple(reasons: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for reason in reasons:
        value = _text(reason, "reason")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _safe_boolean_probe(probe: Callable[[], bool]) -> bool:
    try:
        return probe() is True
    except Exception:
        return False


def _setting_decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("live setting must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError("live setting must be a finite decimal")
    return result


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _sha(value: str, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(character not in _HEX for character in text):
        raise ValueError(f"{name} must be a 64-character SHA")
    return text.lower()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
