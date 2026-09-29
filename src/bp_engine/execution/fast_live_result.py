from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from bp_engine.execution.canary import (
    CANARY_POLICY_VERSION,
    _latest_clean_account_snapshot,
)
from bp_engine.execution.live import _account_snapshot
from bp_engine.live_readiness.models import LiveAccountSnapshot
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema


class FastLiveResultError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FastLiveResultError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _intent(connection, intent_id: str) -> Mapping[str, Any]:
    row = connection.execute(
        select(schema.live_order_intents).where(
            schema.live_order_intents.c.intent_id == intent_id,
            schema.live_order_intents.c.policy_version == CANARY_POLICY_VERSION,
        )
    ).mappings().one_or_none()
    if row is None:
        raise FastLiveResultError("unknown fast-live intent")
    evidence = dict(row["evidence"] or {})
    if evidence.get("phase") != "phase15_v3_fast_live_v1":
        raise FastLiveResultError("intent is not a fast-live intent")
    return row


def _validate_binding(intent: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    evidence = dict(intent["evidence"] or {})
    expected = {
        "prediction_id": str(intent["prediction_id"]),
        "paper_order_id": str(evidence.get("paper_order_id") or ""),
        "request_sha256": str(evidence.get("request_sha256") or ""),
    }
    for name, value in expected.items():
        if not value or str(result.get(name) or "") != value:
            raise FastLiveResultError(f"fast-live result binding mismatch: {name}")


def fast_live_account_snapshot(
    connection,
    *,
    observed_at: datetime,
) -> LiveAccountSnapshot:
    base = _account_snapshot(connection, observed_at=observed_at)
    rows = connection.execute(
        select(schema.live_reconciliation_runs)
        .where(schema.live_reconciliation_runs.c.observed_at <= observed_at)
        .order_by(
            schema.live_reconciliation_runs.c.observed_at.desc(),
            schema.live_reconciliation_runs.c.id.desc(),
        )
    ).mappings().all()
    settled_intents: set[str] = set()
    for row in rows:
        evidence = dict(row["evidence"] or {})
        if evidence.get("reconciliation_kind") != "fast_live_official_settlement":
            continue
        intent_id = str(evidence.get("intent_id") or "")
        if intent_id:
            settled_intents.add(intent_id)

    settled_nominal = Decimal("0")
    if settled_intents:
        intents = connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.intent_id.in_(settled_intents)
            )
        ).mappings().all()
        for intent in intents:
            settled_nominal += (
                Decimal(str(intent["size"]))
                * Decimal(str(intent["limit_price"]))
            )

    return LiveAccountSnapshot(
        total_exposure_usd=max(
            Decimal("0"),
            base.total_exposure_usd - settled_nominal,
        ),
        realized_daily_pnl_usd=base.realized_daily_pnl_usd,
        consecutive_losses=base.consecutive_losses,
        last_order_at=base.last_order_at,
        unresolved_critical_reconciliation=(
            base.unresolved_critical_reconciliation
        ),
    )


def _carry_clean_account(
    connection,
    *,
    observed_at: datetime,
) -> tuple[str, dict[str, object]]:
    latest = _latest_clean_account_snapshot(
        connection,
        observed_at=observed_at,
    )
    if latest is None:
        raise FastLiveResultError("clean account snapshot unavailable")
    return latest


def _carry_pending_fast_account(
    connection,
    *,
    intent_id: str,
    observed_at: datetime,
) -> tuple[str, dict[str, object]]:
    rows = connection.execute(
        select(schema.live_reconciliation_runs)
        .where(schema.live_reconciliation_runs.c.observed_at <= observed_at)
        .order_by(
            schema.live_reconciliation_runs.c.observed_at.desc(),
            schema.live_reconciliation_runs.c.id.desc(),
        )
    ).mappings().all()
    for row in rows:
        evidence = dict(row["evidence"] or {})
        if evidence.get("source") != "phase15_v3_fast_live_result":
            continue
        if evidence.get("intent_id") != intent_id:
            continue
        if (
            evidence.get("reconciliation_kind")
            != "fast_live_submission_pending_official_reconciliation"
        ):
            continue
        raw_account = evidence.get("account_snapshot")
        if not isinstance(raw_account, Mapping):
            raise FastLiveResultError(
                "pending fast-live account snapshot missing"
            )
        try:
            total_exposure = Decimal(
                str(raw_account["total_exposure_usd"])
            )
            realized_pnl = Decimal(
                str(raw_account["realized_daily_pnl_usd"])
            )
            consecutive_losses = int(raw_account["consecutive_losses"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FastLiveResultError(
                "pending fast-live account snapshot invalid"
            ) from exc
        if total_exposure < 0 or consecutive_losses < 0:
            raise FastLiveResultError(
                "pending fast-live account snapshot invalid"
            )
        return (
            str(row["reconciliation_id"]),
            {
                "total_exposure_usd": format(total_exposure, "f"),
                "realized_daily_pnl_usd": format(realized_pnl, "f"),
                "consecutive_losses": consecutive_losses,
            },
        )
    raise FastLiveResultError(
        "pending fast-live reconciliation snapshot unavailable"
    )


def _store_reconciliation(
    repository: LiveReadinessRepository,
    connection,
    *,
    observed_at: datetime,
    intent_id: str,
    external_order_id: str | None,
    status: str,
    submission_attempt_consumed: bool,
    unresolved_count: int,
    critical_count: int,
) -> str:
    carried_from, account_snapshot = _carry_clean_account(
        connection,
        observed_at=observed_at,
    )
    stored = repository.store_reconciliation_run(
        connection,
        observed_at=observed_at,
        unresolved_count=unresolved_count,
        critical_count=critical_count,
        evidence={
            "source": "phase15_v3_fast_live_result",
            "reconciliation_kind": (
                "fast_live_submission_pending_official_reconciliation"
                if critical_count
                else "fast_live_terminal_no_exposure"
            ),
            "intent_id": intent_id,
            "external_order_id": external_order_id,
            "status": status,
            "submission_attempt_consumed": submission_attempt_consumed,
            "account_snapshot": account_snapshot,
            "account_snapshot_carried_from_reconciliation_id": carried_from,
        },
    )
    return str(stored.record["reconciliation_id"])


def record_fast_live_result(
    *,
    engine: Engine,
    result: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, object]:
    observed = _utc(observed_at)
    repository = LiveReadinessRepository()
    intent_id = str(result.get("intent_id") or "")
    status = str(result.get("status") or "")
    external_order_id = str(result.get("external_order_id") or "") or None
    attempt_consumed = result.get("network_submission_attempt_consumed") is True
    if not intent_id or not status:
        raise FastLiveResultError("fast-live result identity missing")

    with engine.begin() as connection:
        intent = _intent(connection, intent_id)
        _validate_binding(intent, result)

        if not attempt_consumed:
            if status not in {
                "fresh_book_rejected",
                "pre_attempt_retry_exhausted",
                "telegram_skipped",
                "telegram_expired",
                "approval_recovery_blocked",
                "pre_submission_blocked",
            }:
                raise FastLiveResultError(
                    "unsupported non-attempt fast-live result"
                )
            repository.store_order_event(
                connection,
                event_key=f"{intent_id}:closed_before_submission",
                intent_id=intent_id,
                event_type="closed_before_submission",
                observed_at=observed,
                external_order_id=None,
                external_trade_id=None,
                evidence={
                    "phase": "phase15_v3_fast_live_v1",
                    "status": status,
                    "reason": str(result.get("reason") or status),
                    "error_type": result.get("error_type"),
                    "marketability": result.get("marketability"),
                    "submission_attempt_consumed": False,
                },
            )
            reconciliation_id = _store_reconciliation(
                repository,
                connection,
                observed_at=observed,
                intent_id=intent_id,
                external_order_id=None,
                status=status,
                submission_attempt_consumed=False,
                unresolved_count=0,
                critical_count=0,
            )
            return {
                "status": "recorded",
                "intent_id": intent_id,
                "event_type": "closed_before_submission",
                "reconciliation_id": reconciliation_id,
                "official_reconciliation_required": False,
            }

        if status == "accepted":
            if not external_order_id:
                raise FastLiveResultError("accepted fast-live result missing order id")
            event_type = "accepted"
        elif status == "rejected":
            event_type = "rejected"
        elif status == "submission_unknown":
            event_type = "submission_unknown"
        elif status == "already_terminal":
            return {
                "status": "already_terminal",
                "intent_id": intent_id,
                "official_reconciliation_required": True,
            }
        else:
            raise FastLiveResultError("unsupported attempted fast-live result status")

        repository.store_order_event(
            connection,
            event_key=f"{intent_id}:{event_type}",
            intent_id=intent_id,
            event_type=event_type,
            observed_at=observed,
            external_order_id=external_order_id,
            external_trade_id=None,
            evidence={
                "phase": "phase15_v3_fast_live_v1",
                "status": status,
                "marketability": result.get("marketability"),
                "quote_source": result.get("quote_source"),
                "source_to_receive_ms": result.get("source_to_receive_ms"),
                "quote_latency_ms": result.get("quote_latency_ms"),
                "sign_latency_ms": result.get("sign_latency_ms"),
                "quote_to_post_ms": result.get("quote_to_post_ms"),
                "post_latency_ms": result.get("post_latency_ms"),
                "submission_attempt_consumed": True,
            },
        )

        cancellation = result.get("cancellation")
        if (
            event_type == "accepted"
            and external_order_id
            and isinstance(cancellation, Mapping)
        ):
            cancelled = cancellation.get("cancelled") is True
            cancel_event = "cancelled" if cancelled else "cancellation_unknown"
            repository.store_order_event(
                connection,
                event_key=f"{intent_id}:{cancel_event}",
                intent_id=intent_id,
                event_type=cancel_event,
                observed_at=observed,
                external_order_id=external_order_id,
                external_trade_id=None,
                evidence={
                    "phase": "phase15_v3_fast_live_v1",
                    "cancellation": dict(cancellation),
                    "ttl_seconds": 2,
                },
            )

        if event_type == "rejected":
            reconciliation_id = _store_reconciliation(
                repository,
                connection,
                observed_at=observed,
                intent_id=intent_id,
                external_order_id=None,
                status=status,
                submission_attempt_consumed=True,
                unresolved_count=0,
                critical_count=0,
            )
            official_required = False
        else:
            reconciliation_id = _store_reconciliation(
                repository,
                connection,
                observed_at=observed,
                intent_id=intent_id,
                external_order_id=external_order_id,
                status=status,
                submission_attempt_consumed=True,
                unresolved_count=1,
                critical_count=1,
            )
            official_required = True

    return {
        "status": "recorded",
        "intent_id": intent_id,
        "event_type": event_type,
        "external_order_id": external_order_id,
        "reconciliation_id": reconciliation_id,
        "official_reconciliation_required": official_required,
    }


def record_fast_live_official_reconciliation(
    *,
    engine: Engine,
    result: Mapping[str, Any],
    official: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, object]:
    observed = _utc(observed_at)
    repository = LiveReadinessRepository()
    intent_id = str(result.get("intent_id") or "")
    external_order_id = str(result.get("external_order_id") or "")
    if not intent_id or not external_order_id:
        raise FastLiveResultError("official reconciliation binding missing")
    if official.get("order_still_open") is True:
        raise FastLiveResultError("official order still open")
    if int(official.get("open_order_count", -1)) != 0:
        raise FastLiveResultError("official open orders present")

    filled_shares = Decimal(str(official.get("confirmed_filled_shares") or "0"))
    filled_notional = Decimal(
        str(official.get("confirmed_filled_notional_usd") or "0")
    )
    if filled_shares < 0 or filled_notional < 0:
        raise FastLiveResultError("official fill totals invalid")

    with engine.begin() as connection:
        intent = _intent(connection, intent_id)
        _validate_binding(intent, result)
        accepted = connection.execute(
            select(schema.live_order_events.c.event_type).where(
                schema.live_order_events.c.intent_id == intent_id,
                schema.live_order_events.c.event_type == "accepted",
                schema.live_order_events.c.external_order_id == external_order_id,
            )
        ).scalar_one_or_none()
        if accepted is None:
            raise FastLiveResultError("accepted event missing before reconciliation")

        carried_from, account_snapshot = _carry_pending_fast_account(
            connection,
            intent_id=intent_id,
            observed_at=observed,
        )
        prior_pnl = Decimal(str(account_snapshot["realized_daily_pnl_usd"]))
        prior_losses = int(account_snapshot["consecutive_losses"])

        if filled_shares == 0:
            exposure = Decimal("0")
            realized_pnl = prior_pnl
            consecutive_losses = prior_losses
            kind = "post_submission_official_zero_fill"
            unresolved_count = 0
            critical_count = 0
        else:
            exposure = filled_notional
            realized_pnl = prior_pnl
            consecutive_losses = prior_losses
            kind = "post_submission_official_fill_open_exposure"
            unresolved_count = 1
            critical_count = 1

        stored = repository.store_reconciliation_run(
            connection,
            observed_at=observed,
            unresolved_count=unresolved_count,
            critical_count=critical_count,
            evidence={
                "source": "phase15_v3_fast_live_official_reconciliation",
                "reconciliation_kind": kind,
                "intent_id": intent_id,
                "external_order_id": external_order_id,
                "official_open_order_count": 0,
                "confirmed_filled_shares": format(filled_shares, "f"),
                "confirmed_filled_notional_usd": format(filled_notional, "f"),
                "official_fill_state": str(official.get("fill_state") or ""),
                "network_submission_attempt_consumed": True,
                "matching_trade_count": int(
                    official.get("matching_trade_count") or 0
                ),
                "account_snapshot": {
                    "total_exposure_usd": format(exposure, "f"),
                    "realized_daily_pnl_usd": format(realized_pnl, "f"),
                    "consecutive_losses": consecutive_losses,
                },
                "account_snapshot_carried_from_reconciliation_id": carried_from,
            },
        )

    return {
        "status": "reconciled",
        "intent_id": intent_id,
        "external_order_id": external_order_id,
        "reconciliation_id": str(stored.record["reconciliation_id"]),
        "confirmed_filled_shares": format(filled_shares, "f"),
        "confirmed_filled_notional_usd": format(filled_notional, "f"),
        "settlement_reconciliation_required": filled_shares > 0,
    }

def settle_fast_live_position_if_resolved(
    *,
    engine: Engine,
    intent_id: str,
    observed_at: datetime,
) -> dict[str, object]:
    observed = _utc(observed_at)
    repository = LiveReadinessRepository()
    normalized_intent_id = str(intent_id).strip()
    if not normalized_intent_id:
        raise FastLiveResultError("settlement intent id missing")

    with engine.begin() as connection:
        intent = _intent(connection, normalized_intent_id)
        evidence = dict(intent["evidence"] or {})
        prediction_id = str(intent["prediction_id"])
        paper_order_id = str(evidence.get("paper_order_id") or "")
        selected_side = str(evidence.get("selected_side") or "").strip().lower()
        if selected_side not in {"up", "down"}:
            raise FastLiveResultError("fast-live selected side missing")
        if not paper_order_id:
            raise FastLiveResultError("fast-live paper order id missing")

        reconciliation_rows = connection.execute(
            select(schema.live_reconciliation_runs).order_by(
                schema.live_reconciliation_runs.c.observed_at.desc(),
                schema.live_reconciliation_runs.c.id.desc(),
            )
        ).mappings().all()

        fill_reconciliation: Mapping[str, Any] | None = None
        for row in reconciliation_rows:
            reconciliation_evidence = dict(row["evidence"] or {})
            if (
                reconciliation_evidence.get("intent_id") == normalized_intent_id
                and reconciliation_evidence.get("reconciliation_kind")
                == "fast_live_official_settlement"
            ):
                return {
                    "status": "already_settled",
                    "intent_id": normalized_intent_id,
                    "reconciliation_id": str(row["reconciliation_id"]),
                    "risk_realized_pnl_usd": str(
                        reconciliation_evidence.get(
                            "risk_realized_pnl_usd",
                            "0",
                        )
                    ),
                }
            if (
                fill_reconciliation is None
                and reconciliation_evidence.get("intent_id")
                == normalized_intent_id
                and reconciliation_evidence.get("reconciliation_kind")
                == "post_submission_official_fill_open_exposure"
            ):
                fill_reconciliation = row

        if fill_reconciliation is None:
            return {
                "status": "waiting",
                "reason": "confirmed_live_fill_not_available",
                "intent_id": normalized_intent_id,
            }

        evaluation = connection.execute(
            select(schema.live_prediction_evaluations)
            .where(
                schema.live_prediction_evaluations.c.prediction_id
                == prediction_id
            )
            .order_by(
                schema.live_prediction_evaluations.c.evaluated_at.desc(),
                schema.live_prediction_evaluations.c.id.desc(),
            )
            .limit(1)
        ).mappings().one_or_none()
        if evaluation is None:
            return {
                "status": "waiting",
                "reason": "official_outcome_not_available",
                "intent_id": normalized_intent_id,
            }

        fee_rate = Decimal(str(evidence.get("modeled_fee_rate") or ""))
        if not Decimal("0") <= fee_rate <= Decimal("1"):
            raise FastLiveResultError("bound modeled fee rate invalid")

        fill_evidence = dict(fill_reconciliation["evidence"] or {})
        filled_shares = Decimal(
            str(fill_evidence.get("confirmed_filled_shares") or "0")
        )
        filled_notional = Decimal(
            str(fill_evidence.get("confirmed_filled_notional_usd") or "0")
        )
        if filled_shares <= 0 or filled_notional <= 0:
            raise FastLiveResultError("settlement requires positive confirmed fill")
        average_fill_price = filled_notional / filled_shares
        if not Decimal("0") < average_fill_price <= Decimal("1"):
            raise FastLiveResultError("average live fill price invalid")

        official_outcome = str(evaluation["official_outcome"]).strip().lower()
        if official_outcome not in {"up", "down"}:
            raise FastLiveResultError("official outcome invalid")
        payout = (
            filled_shares
            if selected_side == official_outcome
            else Decimal("0")
        )
        modeled_fee = (
            filled_shares
            * fee_rate
            * average_fill_price
            * (Decimal("1") - average_fill_price)
        )
        risk_realized_pnl = payout - filled_notional - modeled_fee

        prior_account_raw = fill_evidence.get("account_snapshot")
        if not isinstance(prior_account_raw, Mapping):
            raise FastLiveResultError(
                "fill reconciliation account snapshot missing"
            )
        prior_pnl = Decimal(
            str(prior_account_raw.get("realized_daily_pnl_usd") or "0")
        )
        prior_losses = int(
            prior_account_raw.get("consecutive_losses") or 0
        )
        next_pnl = prior_pnl + risk_realized_pnl
        next_losses = prior_losses + 1 if risk_realized_pnl < 0 else 0

        external_order_id = str(
            fill_evidence.get("external_order_id") or ""
        ) or None
        label_version = str(evaluation["label_version"])
        repository.store_order_event(
            connection,
            event_key=f"{normalized_intent_id}:settled:{label_version}",
            intent_id=normalized_intent_id,
            event_type="settled",
            observed_at=observed,
            external_order_id=external_order_id,
            external_trade_id=None,
            evidence={
                "phase": "phase15_v3_fast_live_v1",
                "paper_order_id": paper_order_id,
                "label_version": label_version,
                "official_outcome": str(evaluation["official_outcome"]),
                "selected_side": selected_side,
                "confirmed_filled_shares": format(filled_shares, "f"),
                "confirmed_filled_notional_usd": format(
                    filled_notional,
                    "f",
                ),
                "modeled_fee_usd": format(modeled_fee, "f"),
                "risk_realized_pnl_usd": format(
                    risk_realized_pnl,
                    "f",
                ),
            },
        )
        stored = repository.store_reconciliation_run(
            connection,
            observed_at=observed,
            unresolved_count=0,
            critical_count=0,
            evidence={
                "source": "phase15_v3_fast_live_settlement",
                "reconciliation_kind": "fast_live_official_settlement",
                "intent_id": normalized_intent_id,
                "external_order_id": external_order_id,
                "paper_order_id": paper_order_id,
                "label_version": label_version,
                "official_outcome": str(evaluation["official_outcome"]),
                "selected_side": selected_side,
                "confirmed_filled_shares": format(
                    filled_shares,
                    "f",
                ),
                "confirmed_filled_notional_usd": format(
                    filled_notional,
                    "f",
                ),
                "average_fill_price": format(
                    average_fill_price,
                    "f",
                ),
                "modeled_fee_rate": format(fee_rate, "f"),
                "modeled_fee_usd": format(modeled_fee, "f"),
                "risk_realized_pnl_usd": format(
                    risk_realized_pnl,
                    "f",
                ),
                "fee_accounting_basis": (
                    "frozen_v3_signal_fee_formula_not_wallet_audited"
                ),
                "account_snapshot": {
                    "total_exposure_usd": "0",
                    "realized_daily_pnl_usd": format(next_pnl, "f"),
                    "consecutive_losses": next_losses,
                },
                "account_snapshot_carried_from_reconciliation_id": str(
                    fill_reconciliation["reconciliation_id"]
                ),
            },
        )

    return {
        "status": "settled",
        "intent_id": normalized_intent_id,
        "reconciliation_id": str(stored.record["reconciliation_id"]),
        "official_outcome": str(evaluation["official_outcome"]),
        "selected_side": selected_side,
        "confirmed_filled_shares": format(filled_shares, "f"),
        "confirmed_filled_notional_usd": format(
            filled_notional,
            "f",
        ),
        "modeled_fee_usd": format(modeled_fee, "f"),
        "risk_realized_pnl_usd": format(
            risk_realized_pnl,
            "f",
        ),
        "consecutive_losses": next_losses,
    }

