from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from bp_engine.live_readiness.hashing import canonical_payload, derive_id, semantic_sha256
from bp_engine.live_readiness.models import LiveRiskDecision
from bp_engine.storage.live_readiness_schema import (
    live_order_events,
    live_order_intents,
    live_readiness_checks,
    live_reconciliation_runs,
    live_risk_decisions,
)


class LiveReadinessConflict(RuntimeError):
    pass


class LiveRiskDecisionConflict(RuntimeError):
    pass


class LiveOrderIntentConflict(RuntimeError):
    pass


class LiveOrderEventConflict(RuntimeError):
    pass


class LiveReconciliationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveStoreResult:
    created: bool
    record: dict[str, Any]


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _sha(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{field} must be a 64-character SHA-256 digest")
    return normalized


def _text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    return normalized


def _decimal(value: Decimal | int | str, field: str) -> Decimal:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field} must be finite")
    return decimal_value


def _row(connection: Connection, table, *conditions) -> dict[str, Any] | None:
    found = connection.execute(select(table).where(*conditions)).mappings().first()
    return None if found is None else dict(found)


def _existing_or_insert(
    connection: Connection,
    *,
    table,
    conditions: tuple[Any, ...],
    values: dict[str, Any],
    conflict_error: type[RuntimeError],
    conflict_message: str,
) -> LiveStoreResult:
    existing = _row(connection, table, *conditions)
    if existing is not None:
        if existing["semantic_sha256"] != values["semantic_sha256"]:
            raise conflict_error(conflict_message)
        return LiveStoreResult(created=False, record=existing)
    connection.execute(insert(table).values(**values))
    created = _row(connection, table, *conditions)
    if created is None:
        raise RuntimeError(f"failed to reload inserted {table.name} row")
    return LiveStoreResult(created=True, record=created)


class LiveReadinessRepository:
    def store_readiness_check(
        self,
        connection: Connection,
        *,
        candidate_git_sha: str,
        observed_at: datetime,
        eligible: bool,
        reasons: tuple[str, ...],
        evidence: dict[str, Any],
        check_id: str | None = None,
    ) -> LiveStoreResult:
        candidate_sha = _sha(candidate_git_sha, "candidate_git_sha")
        observed = _utc(observed_at, "observed_at")
        reasons_payload = canonical_payload(reasons)
        evidence_payload = canonical_payload(evidence)
        payload = {
            "candidate_git_sha": candidate_sha,
            "observed_at": observed,
            "eligible": bool(eligible),
            "reasons": reasons_payload,
            "evidence": evidence_payload,
        }
        digest = semantic_sha256(payload)
        derived_check_id = derive_id("live-readiness", digest)
        chosen_check_id = derived_check_id if check_id is None else _text(check_id, "check_id")
        if chosen_check_id != derived_check_id:
            existing = _row(connection, live_readiness_checks, live_readiness_checks.c.check_id == chosen_check_id)
            if existing is None or existing["semantic_sha256"] != digest:
                raise LiveReadinessConflict("readiness check id collides with different semantic content")
        values = {
            "check_id": chosen_check_id,
            "candidate_git_sha": candidate_sha,
            "observed_at": observed,
            "eligible": bool(eligible),
            "reasons": reasons_payload,
            "evidence": evidence_payload,
            "semantic_sha256": digest,
            "created_at": observed,
        }
        return _existing_or_insert(
            connection,
            table=live_readiness_checks,
            conditions=(live_readiness_checks.c.check_id == chosen_check_id,),
            values=values,
            conflict_error=LiveReadinessConflict,
            conflict_message="readiness check id collides with different semantic content",
        )

    def store_risk_decision(
        self,
        connection: Connection,
        *,
        prediction_id: str,
        prediction_semantic_sha256: str,
        policy_version: str,
        decision: LiveRiskDecision,
        account_snapshot: dict[str, Any],
        evidence: dict[str, Any],
        created_at: datetime,
    ) -> LiveStoreResult:
        prediction = _text(prediction_id, "prediction_id")
        prediction_sha = _sha(prediction_semantic_sha256, "prediction_semantic_sha256")
        version = _text(policy_version, "policy_version")
        created = _utc(created_at, "created_at")
        policy_sha = _sha(decision.policy_sha256, "decision.policy_sha256")
        account_payload = canonical_payload(account_snapshot)
        evidence_payload = canonical_payload(evidence)
        reasons_payload = canonical_payload(decision.reasons)
        rules_payload = canonical_payload(decision.rules)
        payload = {
            "prediction_id": prediction,
            "prediction_semantic_sha256": prediction_sha,
            "policy_version": version,
            "decision": canonical_payload(decision),
            "account_snapshot": account_payload,
            "evidence": evidence_payload,
            "created_at": created,
        }
        digest = semantic_sha256(payload)
        decision_id = derive_id("live-risk", digest)
        values = {
            "decision_id": decision_id,
            "prediction_id": prediction,
            "prediction_semantic_sha256": prediction_sha,
            "policy_version": version,
            "policy_sha256": policy_sha,
            "eligible": decision.eligible,
            "reasons": reasons_payload,
            "rules": rules_payload,
            "account_snapshot": account_payload,
            "evidence": evidence_payload,
            "semantic_sha256": digest,
            "created_at": created,
        }
        return _existing_or_insert(
            connection,
            table=live_risk_decisions,
            conditions=(live_risk_decisions.c.decision_id == decision_id,),
            values=values,
            conflict_error=LiveRiskDecisionConflict,
            conflict_message="risk decision id collides with different semantic content",
        )

    def store_order_intent(
        self,
        connection: Connection,
        *,
        prediction_id: str,
        policy_version: str,
        request_id: str,
        risk_decision_id: str,
        token_id: str,
        side: str,
        size: Decimal | int | str,
        limit_price: Decimal | int | str,
        pre_submit_at: datetime,
        evidence: dict[str, Any],
    ) -> LiveStoreResult:
        prediction = _text(prediction_id, "prediction_id")
        version = _text(policy_version, "policy_version")
        request = _text(request_id, "request_id")
        risk_id = _text(risk_decision_id, "risk_decision_id")
        token = _text(token_id, "token_id")
        normalized_side = _text(side, "side").upper()
        if normalized_side != "BUY":
            raise ValueError("side must be BUY for live readiness v1")
        normalized_size = _decimal(size, "size")
        normalized_price = _decimal(limit_price, "limit_price")
        if normalized_size <= 0:
            raise ValueError("size must be positive")
        if normalized_price <= 0 or normalized_price > 1:
            raise ValueError("limit_price must be within (0, 1]")
        pre_submit = _utc(pre_submit_at, "pre_submit_at")
        evidence_payload = canonical_payload(evidence)
        payload = {
            "prediction_id": prediction,
            "policy_version": version,
            "request_id": request,
            "risk_decision_id": risk_id,
            "token_id": token,
            "side": normalized_side,
            "size": normalized_size,
            "limit_price": normalized_price,
            "pre_submit_at": pre_submit,
            "evidence": evidence_payload,
        }
        digest = semantic_sha256(payload)
        intent_id = derive_id("live-intent", digest)
        values = {
            "intent_id": intent_id,
            "prediction_id": prediction,
            "policy_version": version,
            "request_id": request,
            "risk_decision_id": risk_id,
            "token_id": token,
            "side": normalized_side,
            "size": normalized_size,
            "limit_price": normalized_price,
            "pre_submit_at": pre_submit,
            "evidence": evidence_payload,
            "semantic_sha256": digest,
            "created_at": pre_submit,
        }
        natural_key = (
            live_order_intents.c.prediction_id == prediction,
            live_order_intents.c.policy_version == version,
        )
        return _existing_or_insert(
            connection,
            table=live_order_intents,
            conditions=natural_key,
            values=values,
            conflict_error=LiveOrderIntentConflict,
            conflict_message="live order intent natural key collides with different semantic content",
        )

    def store_order_event(
        self,
        connection: Connection,
        *,
        event_key: str,
        intent_id: str,
        event_type: str,
        observed_at: datetime,
        external_order_id: str | None,
        external_trade_id: str | None,
        evidence: dict[str, Any],
    ) -> LiveStoreResult:
        key = _text(event_key, "event_key")
        intent = _text(intent_id, "intent_id")
        event = _text(event_type, "event_type")
        observed = _utc(observed_at, "observed_at")
        order_id = None if external_order_id is None else _text(external_order_id, "external_order_id")
        trade_id = None if external_trade_id is None else _text(external_trade_id, "external_trade_id")
        evidence_payload = canonical_payload(evidence)
        payload = {
            "event_key": key,
            "intent_id": intent,
            "event_type": event,
            "observed_at": observed,
            "external_order_id": order_id,
            "external_trade_id": trade_id,
            "evidence": evidence_payload,
        }
        digest = semantic_sha256(payload)
        values = {
            **payload,
            "semantic_sha256": digest,
            "created_at": observed,
        }
        return _existing_or_insert(
            connection,
            table=live_order_events,
            conditions=(live_order_events.c.event_key == key,),
            values=values,
            conflict_error=LiveOrderEventConflict,
            conflict_message="live order event key collides with different semantic content",
        )

    def store_reconciliation_run(
        self,
        connection: Connection,
        *,
        observed_at: datetime,
        unresolved_count: int,
        critical_count: int,
        evidence: dict[str, Any],
    ) -> LiveStoreResult:
        observed = _utc(observed_at, "observed_at")
        if unresolved_count < 0:
            raise ValueError("unresolved_count must be nonnegative")
        if critical_count < 0 or critical_count > unresolved_count:
            raise ValueError("critical_count must be nonnegative and no greater than unresolved_count")
        evidence_payload = canonical_payload(evidence)
        payload = {
            "observed_at": observed,
            "unresolved_count": unresolved_count,
            "critical_count": critical_count,
            "evidence": evidence_payload,
        }
        digest = semantic_sha256(payload)
        reconciliation_id = derive_id("live-reconciliation", digest)
        values = {
            "reconciliation_id": reconciliation_id,
            **payload,
            "semantic_sha256": digest,
            "created_at": observed,
        }
        return _existing_or_insert(
            connection,
            table=live_reconciliation_runs,
            conditions=(live_reconciliation_runs.c.reconciliation_id == reconciliation_id,),
            values=values,
            conflict_error=LiveReconciliationConflict,
            conflict_message="reconciliation id collides with different semantic content",
        )
