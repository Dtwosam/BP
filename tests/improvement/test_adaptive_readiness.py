from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import market_features, market_labels, metadata


def _adaptive():
    try:
        return importlib.import_module("bp_engine.improvement.adaptive")
    except ModuleNotFoundError:
        pytest.fail("bp_engine.improvement.adaptive must implement adaptive readiness")


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _label(
    condition_id: str,
    *,
    start: datetime,
    generated_at: datetime,
    horizon_seconds: int = 300,
    label_version: str = "official-outcome-v1",
) -> dict[str, object]:
    end = start + timedelta(seconds=horizon_seconds)
    return {
        "condition_id": condition_id,
        "gamma_market_id": f"gamma-{condition_id}",
        "slug": f"btc-updown-{condition_id}",
        "horizon_seconds": horizon_seconds,
        "market_start_at": start,
        "market_end_at": end,
        "official_outcome": "Up",
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "polymarket",
        "rules_hash": f"rules-{condition_id}",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": label_version,
        "source_snapshot_sha256": (condition_id[0] if condition_id else "a") * 64,
        "source_observed_at": end + timedelta(seconds=1),
        "generated_at": generated_at,
    }


def _feature(
    condition_id: str,
    *,
    start: datetime,
    minute: int = 1,
    horizon_seconds: int = 300,
    feature_version: str = "core-v1",
) -> dict[str, object]:
    end = start + timedelta(seconds=horizon_seconds)
    feature_at = start + timedelta(minutes=minute)
    seed = sum(ord(ch) for ch in condition_id) + minute
    return {
        "condition_id": condition_id,
        "slug": f"btc-updown-{condition_id}",
        "horizon_seconds": horizon_seconds,
        "market_start_at": start,
        "market_end_at": end,
        "feature_at": feature_at,
        "feature_offset_seconds": minute * 60,
        "feature_version": feature_version,
        "features": {"pm_up_price": 0.6},
        "missing_flags": {"pm_up_price_missing": False},
        "source_cutoffs": {"pm_up_price": feature_at.isoformat()},
        "input_fingerprint": f"{seed:064x}"[-64:],
        "feature_hash": f"{seed + 1:064x}"[-64:],
        "generated_at": feature_at + timedelta(seconds=1),
    }


def _report(connection, *, since_at: datetime, cutoff_at: datetime, trigger_count: int = 2):
    adaptive = _adaptive()
    return adaptive.build_adaptive_readiness_report(
        connection,
        horizon_seconds=300,
        feature_version="core-v1",
        label_version="official-outcome-v1",
        since_at=since_at,
        cutoff_at=cutoff_at,
        trigger_count=trigger_count,
    )


def test_readiness_counts_distinct_resolved_markets_with_matching_features() -> None:
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(market_labels),
            [
                _label("a1", start=base, generated_at=base + timedelta(hours=1)),
                _label(
                    "b2",
                    start=base + timedelta(minutes=5),
                    generated_at=base + timedelta(hours=2),
                ),
            ],
        )
        connection.execute(
            insert(market_features),
            [
                _feature("a1", start=base, minute=1),
                _feature("a1", start=base, minute=2),
                _feature("b2", start=base + timedelta(minutes=5), minute=1),
            ],
        )

        report = _report(
            connection,
            since_at=base,
            cutoff_at=base + timedelta(hours=3),
        )

    assert report.readiness_version == "adaptive-readiness-v1"
    assert report.eligible_resolved_market_count == 2
    assert report.ready is True
    assert report.first_label_generated_at == base + timedelta(hours=1)
    assert report.last_label_generated_at == base + timedelta(hours=2)
    assert len(report.condition_ids_sha256) == 64
    assert len(report.semantic_sha256) == 64


def test_readiness_excludes_labels_outside_cycle_window() -> None:
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        for condition_id, generated_at, start_offset in (
            ("aa", base, 0),
            ("bb", base + timedelta(seconds=1), 5),
            ("cc", base + timedelta(hours=1), 10),
            ("dd", base + timedelta(hours=1, seconds=1), 15),
        ):
            start = base + timedelta(minutes=start_offset)
            connection.execute(
                insert(market_labels).values(
                    **_label(condition_id, start=start, generated_at=generated_at)
                )
            )
            connection.execute(
                insert(market_features).values(**_feature(condition_id, start=start))
            )

        report = _report(
            connection,
            since_at=base,
            cutoff_at=base + timedelta(hours=1),
            trigger_count=99,
        )

    assert report.eligible_resolved_market_count == 2
    assert report.ready is False
    assert report.first_label_generated_at == base + timedelta(seconds=1)
    assert report.last_label_generated_at == base + timedelta(hours=1)


def test_readiness_requires_exact_horizon_feature_and_label_versions() -> None:
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        rows = (
            ("ok", 300, "official-outcome-v1", "core-v1"),
            ("wrong-feature", 300, "official-outcome-v1", "other-feature"),
            ("wrong-label", 300, "other-label", "core-v1"),
            ("wrong-horizon", 900, "official-outcome-v1", "core-v1"),
        )
        for index, (condition_id, horizon, label_version, feature_version) in enumerate(rows):
            start = base + timedelta(minutes=index * 20)
            connection.execute(
                insert(market_labels).values(
                    **_label(
                        condition_id,
                        start=start,
                        generated_at=base + timedelta(hours=1, minutes=index),
                        horizon_seconds=horizon,
                        label_version=label_version,
                    )
                )
            )
            connection.execute(
                insert(market_features).values(
                    **_feature(
                        condition_id,
                        start=start,
                        horizon_seconds=horizon,
                        feature_version=feature_version,
                    )
                )
            )

        report = _report(
            connection,
            since_at=base,
            cutoff_at=base + timedelta(hours=3),
            trigger_count=2,
        )

    assert report.eligible_resolved_market_count == 1
    assert report.ready is False


def test_readiness_excludes_labeled_market_without_requested_feature() -> None:
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(market_labels).values(
                **_label("label-only", start=base, generated_at=base + timedelta(hours=1))
            )
        )
        report = _report(
            connection,
            since_at=base,
            cutoff_at=base + timedelta(hours=2),
            trigger_count=1,
        )

    assert report.eligible_resolved_market_count == 0
    assert report.first_label_generated_at is None
    assert report.last_label_generated_at is None
    assert report.ready is False


def test_readiness_does_not_require_an_executed_trade() -> None:
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        connection.execute(
            insert(market_labels).values(
                **_label("no-trade", start=base, generated_at=base + timedelta(hours=1))
            )
        )
        connection.execute(insert(market_features).values(**_feature("no-trade", start=base)))
        report = _report(
            connection,
            since_at=base,
            cutoff_at=base + timedelta(hours=2),
            trigger_count=1,
        )

    assert report.eligible_resolved_market_count == 1
    assert report.ready is True


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"horizon_seconds": 0}, "horizon_seconds"),
        ({"feature_version": ""}, "feature_version"),
        ({"label_version": ""}, "label_version"),
        ({"trigger_count": 0}, "trigger_count"),
    ],
)
def test_readiness_rejects_invalid_contract_values(kwargs: dict[str, object], match: str) -> None:
    adaptive = _adaptive()
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    arguments: dict[str, object] = {
        "horizon_seconds": 300,
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "since_at": base,
        "cutoff_at": base + timedelta(hours=1),
        "trigger_count": 50,
    }
    arguments.update(kwargs)
    with engine.begin() as connection, pytest.raises(ValueError, match=match):
        adaptive.build_adaptive_readiness_report(connection, **arguments)


def test_readiness_rejects_naive_or_reversed_time_window() -> None:
    adaptive = _adaptive()
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    engine = _engine()
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="since_at"):
            adaptive.build_adaptive_readiness_report(
                connection,
                horizon_seconds=300,
                feature_version="core-v1",
                label_version="official-outcome-v1",
                since_at=datetime(2026, 9, 12),
                cutoff_at=base + timedelta(hours=1),
            )
        with pytest.raises(ValueError, match="cutoff_at"):
            adaptive.build_adaptive_readiness_report(
                connection,
                horizon_seconds=300,
                feature_version="core-v1",
                label_version="official-outcome-v1",
                since_at=base,
                cutoff_at=base,
            )


def test_readiness_hashes_are_deterministic_across_insert_order() -> None:
    adaptive = _adaptive()
    base = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)

    def make_report(order: tuple[str, ...]):
        engine = _engine()
        with engine.begin() as connection:
            for index, condition_id in enumerate(order):
                start = base + timedelta(minutes=index * 5)
                connection.execute(
                    insert(market_labels).values(
                        **_label(
                            condition_id,
                            start=start,
                            generated_at=base + timedelta(hours=1),
                        )
                    )
                )
                connection.execute(
                    insert(market_features).values(**_feature(condition_id, start=start))
                )
            return adaptive.build_adaptive_readiness_report(
                connection,
                horizon_seconds=300,
                feature_version="core-v1",
                label_version="official-outcome-v1",
                since_at=base,
                cutoff_at=base + timedelta(hours=2),
                trigger_count=2,
            )

    first = make_report(("aa", "bb"))
    second = make_report(("bb", "aa"))

    assert first.condition_ids_sha256 == second.condition_ids_sha256
    assert first.semantic_sha256 == second.semantic_sha256
