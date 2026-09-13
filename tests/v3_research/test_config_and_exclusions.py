from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.backtesting.models import WalkForwardConfig
from bp_engine.features.hashing import canonical_hash

try:
    from bp_engine.v3_research.config import FROZEN_V3_GATE_B_CONFIG
    from bp_engine.v3_research.exclusions import (
        EXCLUSION_MANIFEST_VERSION,
        ExclusionManifestError,
        build_exclusion_manifest,
        load_exclusion_manifest,
    )
except ModuleNotFoundError:
    FROZEN_V3_GATE_B_CONFIG = None
    EXCLUSION_MANIFEST_VERSION = None
    ExclusionManifestError = ValueError
    build_exclusion_manifest = None
    load_exclusion_manifest = None


def test_frozen_v3_gate_b_config_matches_preregistered_contract() -> None:
    assert FROZEN_V3_GATE_B_CONFIG is not None
    config = FROZEN_V3_GATE_B_CONFIG

    assert config.research_plan_version == "v3-gate-b-preregister-v1"
    assert config.dataset_version == "supervised-core-v3-btc-native-v1"
    assert config.feature_version == "core-v3-btc-native"
    assert config.label_version == "official-outcome-v1"
    assert config.horizon_seconds == 300
    assert config.feature_offsets_seconds == (60, 120, 180, 240)
    assert config.epoch_start == datetime(2026, 9, 13, 13, 45, tzinfo=UTC)
    assert config.epoch_end == datetime(2026, 9, 16, 13, 45, tzinfo=UTC)
    assert config.epoch_end - config.epoch_start == timedelta(hours=72)

    assert config.train_duration == timedelta(hours=24)
    assert config.validation_duration == timedelta(hours=6)
    assert config.test_duration == timedelta(hours=6)
    assert config.step_duration == timedelta(hours=6)
    assert config.final_holdout_duration == timedelta(hours=12)
    assert config.embargo_markets == 1
    assert config.min_train_markets == 240
    assert config.min_validation_markets == 60
    assert config.min_test_markets == 60
    assert config.min_final_holdout_markets == 120
    assert config.ordinary_fold_count == 5


def test_reserved_window_minimum_remains_v3_local() -> None:
    assert FROZEN_V3_GATE_B_CONFIG is not None
    assert FROZEN_V3_GATE_B_CONFIG.min_final_holdout_markets == 120
    assert "min_final_holdout_markets" not in WalkForwardConfig.__dataclass_fields__


def test_exclusion_manifest_sorts_deduplicates_and_hash_binds_payload() -> None:
    assert build_exclusion_manifest is not None
    manifest = build_exclusion_manifest(
        kind="diagnosis",
        condition_ids=("condition-b", "condition-a", "condition-a"),
    )

    assert manifest.version == EXCLUSION_MANIFEST_VERSION
    assert manifest.kind == "diagnosis"
    assert manifest.condition_ids == ("condition-a", "condition-b")
    assert manifest.sha256 == canonical_hash(
        {
            "version": EXCLUSION_MANIFEST_VERSION,
            "kind": "diagnosis",
            "condition_ids": ["condition-a", "condition-b"],
        }
    )


def test_exclusion_manifest_loader_rejects_tampering(tmp_path) -> None:
    assert build_exclusion_manifest is not None
    assert load_exclusion_manifest is not None
    manifest = build_exclusion_manifest(
        kind="consumed_v2_final_holdout",
        condition_ids=("condition-a", "condition-b"),
    )
    path = tmp_path / "consumed-v2.json"
    payload = manifest.to_dict()
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_exclusion_manifest(path, expected_kind="consumed_v2_final_holdout")
    assert loaded == manifest

    payload["condition_ids"] = ["condition-a", "condition-c"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExclusionManifestError, match="hash"):
        load_exclusion_manifest(path, expected_kind="consumed_v2_final_holdout")


def test_exclusion_manifest_loader_requires_canonical_ids_and_expected_kind(tmp_path) -> None:
    assert build_exclusion_manifest is not None
    assert load_exclusion_manifest is not None
    diagnosis = build_exclusion_manifest(
        kind="diagnosis",
        condition_ids=("condition-a", "condition-b"),
    )
    path = tmp_path / "diagnosis.json"

    noncanonical_payload = diagnosis.to_dict()
    noncanonical_payload["condition_ids"] = ["condition-b", "condition-a"]
    noncanonical_payload["sha256"] = canonical_hash(
        {
            "version": EXCLUSION_MANIFEST_VERSION,
            "kind": "diagnosis",
            "condition_ids": ["condition-b", "condition-a"],
        }
    )
    path.write_text(json.dumps(noncanonical_payload), encoding="utf-8")
    with pytest.raises(ExclusionManifestError, match="sorted and unique"):
        load_exclusion_manifest(path, expected_kind="diagnosis")

    path.write_text(json.dumps(diagnosis.to_dict()), encoding="utf-8")
    with pytest.raises(ExclusionManifestError, match="expected kind"):
        load_exclusion_manifest(path, expected_kind="consumed_v2_final_holdout")


def test_exclusion_manifest_allows_only_preregistered_kinds() -> None:
    assert build_exclusion_manifest is not None
    with pytest.raises(ExclusionManifestError, match="kind"):
        build_exclusion_manifest(kind="other", condition_ids=("condition-a",))
