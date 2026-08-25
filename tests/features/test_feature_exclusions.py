from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta


def _exclusions():
    return importlib.import_module("bp_engine.features.exclusions")


def test_known_phase3_raw_exclusions_are_encoded_exactly() -> None:
    exclusions = _exclusions()

    assert exclusions.RAW_EXCLUSIONS == (
        exclusions.RawExclusion(
            start=datetime(2026, 8, 22, 20, 0, tzinfo=UTC),
            end=datetime(2026, 8, 22, 21, 0, tzinfo=UTC),
            reason="phase3_known_damaged_raw_interval",
        ),
        exclusions.RawExclusion(
            start=datetime(2026, 8, 23, 21, 0, tzinfo=UTC),
            end=datetime(2026, 8, 24, 10, 18, 16, 692122, tzinfo=UTC),
            reason="phase3_rollout_raw_coverage_unproven",
        ),
    )


def test_any_half_open_window_overlap_is_excluded() -> None:
    exclusions = _exclusions()
    damaged_start = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)
    damaged_end = datetime(2026, 8, 22, 21, 0, tzinfo=UTC)

    assert exclusions.raw_window_exclusion(
        damaged_start - timedelta(seconds=1), damaged_start
    ) is None
    assert exclusions.raw_window_exclusion(
        damaged_start - timedelta(seconds=1), damaged_start + timedelta(microseconds=1)
    ) is not None
    assert exclusions.raw_window_exclusion(
        damaged_end - timedelta(microseconds=1), damaged_end
    ) is not None
    assert exclusions.raw_window_exclusion(
        damaged_end, damaged_end + timedelta(seconds=1)
    ) is None


def test_raw_exclusion_rejects_naive_or_non_increasing_windows() -> None:
    exclusions = _exclusions()
    aware = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)

    for start, end in ((naive, aware), (aware, naive), (aware, aware)):
        try:
            exclusions.raw_window_exclusion(start, end)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid raw window must fail closed")
