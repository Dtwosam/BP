from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bp_engine.execution.book import (
    BookLevel,
    BookReplayError,
    replay_book_payloads,
)


def _anchor() -> dict[str, object]:
    return {
        "event_type": "book",
        "market": "condition-1",
        "asset_id": "up-token",
        "bids": [
            {"price": "0.54", "size": "10"},
            {"price": "0.53", "size": "4"},
        ],
        "asks": [
            {"price": "0.56", "size": "2"},
            {"price": "0.57", "size": "4"},
        ],
    }


def _change(*changes: dict[str, str]) -> dict[str, object]:
    return {
        "event_type": "price_change",
        "market": "condition-1",
        "price_changes": list(changes),
    }


def test_replay_book_applies_selected_token_changes_and_tracks_provenance() -> None:
    cutoff = datetime(2026, 8, 28, 16, 30, 0, 500000, tzinfo=UTC)
    replayed = replay_book_payloads(
        condition_id="condition-1",
        asset_id="up-token",
        anchor_event_id=101,
        anchor_dedupe_key="a" * 64,
        anchor_payload=_anchor(),
        applied_events=(
            (
                102,
                "b" * 64,
                _change(
                    {
                        "asset_id": "up-token",
                        "side": "SELL",
                        "price": "0.56",
                        "size": "1.5",
                    },
                    {
                        "asset_id": "down-token",
                        "side": "SELL",
                        "price": "0.20",
                        "size": "100",
                    },
                ),
            ),
            (
                103,
                "c" * 64,
                _change(
                    {
                        "asset_id": "up-token",
                        "side": "BUY",
                        "price": "0.55",
                        "size": "3",
                    },
                    {
                        "asset_id": "up-token",
                        "side": "SELL",
                        "price": "0.57",
                        "size": "0",
                    },
                ),
            ),
        ),
        replay_cutoff_at=cutoff,
    )

    assert replayed.bids == (
        BookLevel(price=Decimal("0.55"), size=Decimal("3")),
        BookLevel(price=Decimal("0.54"), size=Decimal("10")),
        BookLevel(price=Decimal("0.53"), size=Decimal("4")),
    )
    assert replayed.asks == (
        BookLevel(price=Decimal("0.56"), size=Decimal("1.5")),
    )
    assert replayed.anchor_event_id == 101
    assert replayed.anchor_dedupe_key == "a" * 64
    assert replayed.applied_event_ids == (102, 103)
    assert replayed.applied_dedupe_keys == ("b" * 64, "c" * 64)
    assert replayed.replay_cutoff_at == cutoff


@pytest.mark.parametrize(
    "payload",
    [
        {
            "event_type": "book",
            "market": "condition-1",
            "asset_id": "up-token",
            "bids": [{"price": "nan", "size": "1"}],
            "asks": [],
        },
        {
            "event_type": "book",
            "market": "condition-1",
            "asset_id": "up-token",
            "bids": [{"price": "0.50", "size": "-1"}],
            "asks": [],
        },
        {
            "event_type": "book",
            "market": "condition-1",
            "asset_id": "other-token",
            "bids": [{"price": "0.50", "size": "1"}],
            "asks": [],
        },
    ],
)
def test_replay_book_rejects_malformed_or_conflicting_anchor(
    payload: dict[str, object],
) -> None:
    with pytest.raises(BookReplayError):
        replay_book_payloads(
            condition_id="condition-1",
            asset_id="up-token",
            anchor_event_id=101,
            anchor_dedupe_key="a" * 64,
            anchor_payload=payload,
            applied_events=(),
            replay_cutoff_at=datetime(2026, 8, 28, 16, 30, tzinfo=UTC),
        )


def test_replay_book_rejects_crossed_or_invalid_change_semantics() -> None:
    with pytest.raises(BookReplayError, match="crossed"):
        replay_book_payloads(
            condition_id="condition-1",
            asset_id="up-token",
            anchor_event_id=101,
            anchor_dedupe_key="a" * 64,
            anchor_payload=_anchor(),
            applied_events=(
                (
                    102,
                    "b" * 64,
                    _change(
                        {
                            "asset_id": "up-token",
                            "side": "BUY",
                            "price": "0.58",
                            "size": "1",
                        }
                    ),
                ),
            ),
            replay_cutoff_at=datetime(2026, 8, 28, 16, 30, tzinfo=UTC),
        )

    with pytest.raises(BookReplayError, match="side"):
        replay_book_payloads(
            condition_id="condition-1",
            asset_id="up-token",
            anchor_event_id=101,
            anchor_dedupe_key="a" * 64,
            anchor_payload=_anchor(),
            applied_events=(
                (
                    102,
                    "b" * 64,
                    _change(
                        {
                            "asset_id": "up-token",
                            "side": "UNKNOWN",
                            "price": "0.55",
                            "size": "1",
                        }
                    ),
                ),
            ),
            replay_cutoff_at=datetime(2026, 8, 28, 16, 30, tzinfo=UTC),
        )
