import importlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest


def _hashing():
    return importlib.import_module("bp_engine.features.hashing")


def test_canonical_hash_is_mapping_order_independent() -> None:
    hashing = _hashing()
    left = {
        "b": Decimal("1.2300"),
        "a": datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        "nested": {"y": 2, "x": 1},
    }
    right = {
        "nested": {"x": 1, "y": 2},
        "a": datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
        "b": Decimal("1.23"),
    }
    assert hashing.canonical_hash(left) == hashing.canonical_hash(right)


def test_canonical_hash_changes_when_sequence_order_changes() -> None:
    hashing = _hashing()
    assert hashing.canonical_hash([1, 2, 3]) != hashing.canonical_hash([3, 2, 1])


def test_canonical_hash_rejects_naive_datetime() -> None:
    hashing = _hashing()
    with pytest.raises(ValueError, match="timezone-aware"):
        hashing.canonical_hash({"at": datetime(2026, 8, 25, 12, 0)})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_hash_rejects_non_finite_float(value: float) -> None:
    hashing = _hashing()
    with pytest.raises(ValueError, match="finite"):
        hashing.canonical_hash({"value": value})
