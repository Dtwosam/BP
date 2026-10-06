from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import joblib
import numpy as np
import pytest
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.impute import SimpleImputer

import bp_engine.v4_paper.inference as inference_module
from bp_engine.v4_paper.inference import (
    FROZEN_V4_CANDIDATE,
    FROZEN_V4_DATASET_SHA256,
    FROZEN_V4_EDGE_POLICY,
    FROZEN_V4_FAMILY,
    FROZEN_V4_LEGACY_BOOK_AGE_SECONDS,
    FROZEN_V4_PLAN_SHA256,
    FROZEN_V4_RESEARCH_PLAN_VERSION,
    FROZEN_V4_SKLEARN_VERSION,
    FrozenV4ModelError,
    load_frozen_v4_bundle,
    predict_frozen_v4_probability,
)


class _Estimator:
    def predict_proba(self, matrix):
        assert matrix.shape == (1, 2)
        assert np.isfinite(matrix).all()
        return np.asarray([[0.25, 0.75]], dtype=float)


def _bundle() -> dict[str, object]:
    imputer = SimpleImputer(strategy="median")
    imputer.fit(np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=float))
    return {
        "candidate": FROZEN_V4_CANDIDATE,
        "family": FROZEN_V4_FAMILY,
        "offset_seconds": 240,
        "predictor_names": ("a", "b"),
        "dropped_all_missing": (),
        "imputer": imputer,
        "estimator": _Estimator(),
        "config": {"test": True},
        "research_plan_version": FROZEN_V4_RESEARCH_PLAN_VERSION,
        "plan_sha256": FROZEN_V4_PLAN_SHA256,
        "dataset_sha256_non_holdout": FROZEN_V4_DATASET_SHA256,
        "calibration_fit": {"method": "identity"},
        "calibration_method": "identity",
        "selected_min_edge": 0.05,
        "edge_policy": FROZEN_V4_EDGE_POLICY,
        "fee_rate": 0.07,
        "slippage_buffer": 0.01,
        "max_selected_book_age_seconds": FROZEN_V4_LEGACY_BOOK_AGE_SECONDS,
    }


def test_load_hashes_before_trusted_deserialization(tmp_path: Path) -> None:
    path = tmp_path / "model.joblib"
    joblib.dump(_bundle(), path)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    loaded = load_frozen_v4_bundle(
        path,
        expected_sha256=digest,
        expected_size_bytes=len(payload),
    )
    assert loaded["candidate"] == FROZEN_V4_CANDIDATE

    with pytest.raises(FrozenV4ModelError, match="SHA-256"):
        load_frozen_v4_bundle(
            path,
            expected_sha256="0" * 64,
            expected_size_bytes=len(payload),
        )


def test_load_fails_closed_before_deserialization_on_runtime_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "model.joblib"
    joblib.dump(_bundle(), path)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(
        inference_module,
        "package_version",
        lambda name: "1.9.0" if name == "scikit-learn" else "unexpected",
    )

    def _unexpected_load(source):
        raise AssertionError("joblib.load must not run on a mismatched sklearn runtime")

    monkeypatch.setattr(joblib, "load", _unexpected_load)

    with pytest.raises(
        FrozenV4ModelError,
        match=r"scikit-learn runtime mismatch: required=1\.9\.1 current=1\.9\.0",
    ):
        load_frozen_v4_bundle(
            path,
            expected_sha256=digest,
            expected_size_bytes=len(payload),
        )


def test_load_fails_closed_on_embedded_sklearn_version_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "model.joblib"
    joblib.dump(_bundle(), path)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(
        inference_module,
        "package_version",
        lambda name: FROZEN_V4_SKLEARN_VERSION,
    )
    original_load = joblib.load

    def _mismatched_load(source):
        warnings.warn(
            InconsistentVersionWarning(
                estimator_name="SimpleImputer",
                current_sklearn_version=FROZEN_V4_SKLEARN_VERSION,
                original_sklearn_version="1.9.0",
            ),
            stacklevel=2,
        )
        return original_load(source)

    monkeypatch.setattr(joblib, "load", _mismatched_load)

    with pytest.raises(
        FrozenV4ModelError,
        match=r"scikit-learn version mismatch: artifact=1\.9\.0 runtime=1\.9\.1",
    ):
        load_frozen_v4_bundle(
            path,
            expected_sha256=digest,
            expected_size_bytes=len(payload),
        )


def test_frozen_v4_inference_uses_bundle_order_and_imputer() -> None:
    probability = predict_frozen_v4_probability(
        _bundle(),
        {"a": 1.5, "b": None},
    )
    assert probability == pytest.approx(0.75)


def test_frozen_v4_inference_fails_closed_on_schema_drift() -> None:
    with pytest.raises(FrozenV4ModelError, match="missing key: b"):
        predict_frozen_v4_probability(_bundle(), {"a": 1.5})
