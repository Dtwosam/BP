from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pytest

from bp_engine.modeling.cli import build_parser, parse_datetime, validate_window


def test_parse_datetime_requires_timezone_and_normalizes_utc() -> None:
    assert parse_datetime("2026-08-24T01:00:00+01:00") == datetime(
        2026, 8, 24, 0, 0, tzinfo=UTC
    )
    with pytest.raises(argparse.ArgumentTypeError, match="timezone"):
        parse_datetime("2026-08-24T00:00:00")


def test_training_parser_defaults_to_verified_horizons_and_frozen_versions() -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-08-24T00:00:00Z",
            "--end",
            "2026-08-25T00:00:00Z",
            "--output-dir",
            "/tmp/models",
        ]
    )

    assert args.feature_version == "core-v1"
    assert args.label_version == "official-outcome-v1"
    assert args.horizon_seconds is None
    assert args.output_dir == "/tmp/models"


def test_validate_window_rejects_non_increasing_window() -> None:
    instant = datetime(2026, 8, 24, tzinfo=UTC)
    with pytest.raises(ValueError, match="before"):
        validate_window(instant, instant)
