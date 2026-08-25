import json
from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.labels.service import (
    LabelLeakageError,
    LabelSourceConflict,
    generate_labels,
)
from sqlalchemy import create_engine, insert, select

from bp_engine.storage.schema import market_labels, metadata, polymarket_market_snapshots


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _payload(
    start: datetime,
    *,
    condition_id: str = "condition-1",
    gamma_market_id: str = "market-1",
    outcome: str | None = "Up",
    closed: bool = True,
    rules_suffix: str = "",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    slug = f"btc-updown-5m-{int(start.timestamp())}"
    if outcome == "Up":
        prices = ["1", "0"]
    elif outcome == "Down":
        prices = ["0", "1"]
    else:
        prices = ["0.5", "0.5"]
    payload: dict[str, object] = {
        "id": gamma_market_id,
        "conditionId": condition_id,
        "slug": slug,
        "question": "Bitcoin Up or Down",
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "description": f"Official BTC resolution rule{rules_suffix}",
        "outcomes": json.dumps(["Up", "Down"]),
        "outcomePrices": json.dumps(prices),
        "clobTokenIds": json.dumps(["up-token", "down-token"]),
        "active": not closed,
        "closed": closed,
        "acceptingOrders": not closed,
        "events": [{"id": "event-1"}],
    }
    if extra:
        payload.update(extra)
    return payload


def _snapshot_values(
    start: datetime,
    downloaded_at: datetime,
    sha: str,
    *,
    payload: dict[str, object] | None = None,
    envelope_condition_id: str | None = None,
    envelope_slug: str | None = None,
) -> dict[str, object]:
    actual = payload or _payload(start)
    return {
        "condition_id": envelope_condition_id or str(actual["conditionId"]),
        "gamma_market_id": str(actual["id"]),
        "slug": envelope_slug or str(actual["slug"]),
        "downloaded_at": downloaded_at,
        "payload_sha256": sha,
        "payload": actual,
    }


def test_unresolved_conditions_are_skipped_and_never_persisted() -> None:
    engine = _engine()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    open_payload = _payload(start, condition_id="open", outcome=None, closed=False)
    ambiguous_payload = _payload(
        start + timedelta(minutes=5),
        condition_id="ambiguous",
        outcome=None,
        closed=True,
    )

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots),
            [
                _snapshot_values(
                    start,
                    start + timedelta(minutes=6),
                    "sha256:open",
                    payload=open_payload,
                ),
                _snapshot_values(
                    start + timedelta(minutes=5),
                    start + timedelta(minutes=11),
                    "sha256:ambiguous",
                    payload=ambiguous_payload,
                ),
            ],
        )
        stats = generate_labels(
            connection,
            start=start,
            end=start + timedelta(minutes=10),
            generated_at=start + timedelta(minutes=20),
        )
        labels = connection.execute(select(market_labels)).all()

    assert stats.conditions_considered == 2
    assert stats.inserted == 0
    assert stats.existing == 0
    assert stats.skipped == 2
    assert labels == []


def test_resolved_snapshot_observed_before_market_end_is_rejected() -> None:
    engine = _engine()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots).values(
                **_snapshot_values(
                    start,
                    start + timedelta(minutes=4, seconds=59),
                    "sha256:too-early",
                )
            )
        )
        with pytest.raises(LabelLeakageError, match="condition-1"):
            generate_labels(
                connection,
                start=start,
                end=start + timedelta(minutes=5),
                generated_at=start + timedelta(minutes=10),
            )


def test_snapshot_envelope_mismatch_fails_closed() -> None:
    engine = _engine()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots).values(
                **_snapshot_values(
                    start,
                    start + timedelta(minutes=6),
                    "sha256:mismatch",
                    envelope_condition_id="wrong-condition",
                )
            )
        )
        with pytest.raises(LabelSourceConflict, match="envelope"):
            generate_labels(
                connection,
                start=start,
                end=start + timedelta(minutes=5),
                generated_at=start + timedelta(minutes=10),
            )


def test_conflicting_resolved_snapshots_for_one_condition_fail_closed() -> None:
    engine = _engine()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    up_payload = _payload(start, outcome="Up")
    down_payload = _payload(start, outcome="Down", extra={"updatedAt": "later"})

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots),
            [
                _snapshot_values(
                    start,
                    start + timedelta(minutes=6),
                    "sha256:up",
                    payload=up_payload,
                ),
                _snapshot_values(
                    start,
                    start + timedelta(minutes=7),
                    "sha256:down",
                    payload=down_payload,
                ),
            ],
        )
        with pytest.raises(LabelSourceConflict, match="condition-1"):
            generate_labels(
                connection,
                start=start,
                end=start + timedelta(minutes=5),
                generated_at=start + timedelta(minutes=10),
            )


def test_earliest_agreeing_resolved_snapshot_is_canonical_and_rerun_is_existing() -> None:
    engine = _engine()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    later_payload = _payload(start, outcome="Up", extra={"updatedAt": "later"})
    earlier_payload = _payload(start, outcome="Up", extra={"updatedAt": "earlier"})

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots),
            [
                _snapshot_values(
                    start,
                    start + timedelta(minutes=7),
                    "sha256:later",
                    payload=later_payload,
                ),
                _snapshot_values(
                    start,
                    start + timedelta(minutes=6),
                    "sha256:earlier",
                    payload=earlier_payload,
                ),
            ],
        )
        first = generate_labels(
            connection,
            start=start,
            end=start + timedelta(minutes=5),
            generated_at=start + timedelta(minutes=10),
        )
        second = generate_labels(
            connection,
            start=start,
            end=start + timedelta(minutes=5),
            generated_at=start + timedelta(minutes=20),
        )
        stored = connection.execute(select(market_labels)).mappings().one()

    assert first.conditions_considered == 1
    assert first.inserted == 1
    assert first.existing == 0
    assert first.skipped == 0
    assert second.inserted == 0
    assert second.existing == 1
    assert stored["official_outcome"] == "Up"
    assert stored["source_snapshot_sha256"] == "sha256:earlier"
    assert stored["start_reference"] is None
    assert stored["end_reference"] is None


def test_generation_window_is_half_open_on_market_start() -> None:
    engine = _engine()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    at_end = start + timedelta(minutes=5)
    payload = _payload(at_end, condition_id="at-end")

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_market_snapshots).values(
                **_snapshot_values(
                    at_end,
                    at_end + timedelta(minutes=6),
                    "sha256:at-end",
                    payload=payload,
                )
            )
        )
        stats = generate_labels(
            connection,
            start=start,
            end=at_end,
            generated_at=at_end + timedelta(minutes=10),
        )

    assert stats.conditions_considered == 0
    assert stats.inserted == 0
    assert stats.skipped == 0
