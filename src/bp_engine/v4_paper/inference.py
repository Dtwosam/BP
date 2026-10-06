from __future__ import annotations

import hashlib
import io
import math
import warnings
from collections.abc import Mapping
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.exceptions import InconsistentVersionWarning

FROZEN_V4_MODEL_SHA256 = (
    "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
)
FROZEN_V4_MODEL_SIZE_BYTES = 230132
FROZEN_V4_PLAN_SHA256 = (
    "9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"
)
FROZEN_V4_DATASET_SHA256 = (
    "0de3d89a8a15c4f0c1d0823ec088a70483e9fb83154f06a3747b1771471c97e4"
)
FROZEN_V4_RESEARCH_PLAN_VERSION = "v4-gate-b-preregister-v2"
FROZEN_V4_CANDIDATE = "full_v4_xgboost"
FROZEN_V4_FAMILY = "xgboost"
FROZEN_V4_OFFSET_SECONDS = 240
FROZEN_V4_CALIBRATION_METHOD = "identity"
FROZEN_V4_EDGE_POLICY = "trade_threshold"
FROZEN_V4_MIN_EDGE = Decimal("0.05")
FROZEN_V4_FEE_RATE = Decimal("0.07")
FROZEN_V4_SLIPPAGE_BUFFER = Decimal("0.01")
FROZEN_V4_LEGACY_BOOK_AGE_SECONDS = 10
FROZEN_V4_SKLEARN_VERSION = "1.9.1"


class FrozenV4ModelError(RuntimeError):
    """Raised when the frozen V4 artifact or inference contract is invalid."""


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise FrozenV4ModelError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise FrozenV4ModelError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise FrozenV4ModelError(f"{name} must be finite")
    return result


def validate_frozen_v4_bundle(bundle: Mapping[str, Any]) -> None:
    expected = {
        "candidate": FROZEN_V4_CANDIDATE,
        "family": FROZEN_V4_FAMILY,
        "offset_seconds": FROZEN_V4_OFFSET_SECONDS,
        "research_plan_version": FROZEN_V4_RESEARCH_PLAN_VERSION,
        "plan_sha256": FROZEN_V4_PLAN_SHA256,
        "dataset_sha256_non_holdout": FROZEN_V4_DATASET_SHA256,
        "calibration_method": FROZEN_V4_CALIBRATION_METHOD,
        "edge_policy": FROZEN_V4_EDGE_POLICY,
        "max_selected_book_age_seconds": FROZEN_V4_LEGACY_BOOK_AGE_SECONDS,
    }
    for key, value in expected.items():
        if bundle.get(key) != value:
            raise FrozenV4ModelError(f"frozen V4 bundle mismatch: {key}")

    numeric_expected = {
        "selected_min_edge": FROZEN_V4_MIN_EDGE,
        "fee_rate": FROZEN_V4_FEE_RATE,
        "slippage_buffer": FROZEN_V4_SLIPPAGE_BUFFER,
    }
    for key, value in numeric_expected.items():
        if _decimal(bundle.get(key), key) != value:
            raise FrozenV4ModelError(f"frozen V4 bundle mismatch: {key}")

    names = bundle.get("predictor_names")
    if not isinstance(names, (tuple, list)) or not names:
        raise FrozenV4ModelError("frozen V4 predictor_names missing")
    normalized_names = tuple(str(name) for name in names)
    if len(normalized_names) != len(set(normalized_names)):
        raise FrozenV4ModelError("frozen V4 predictor_names contain duplicates")
    if any(not name for name in normalized_names):
        raise FrozenV4ModelError("frozen V4 predictor_names contain empty names")

    if bundle.get("imputer") is None:
        raise FrozenV4ModelError("frozen V4 imputer missing")
    if bundle.get("estimator") is None:
        raise FrozenV4ModelError("frozen V4 estimator missing")


def load_frozen_v4_bundle(
    path: str | Path,
    *,
    expected_sha256: str = FROZEN_V4_MODEL_SHA256,
    expected_size_bytes: int = FROZEN_V4_MODEL_SIZE_BYTES,
) -> dict[str, Any]:
    """Hash and size-check the artifact bytes before deserializing trusted joblib."""

    source = Path(path)
    payload = source.read_bytes()
    if len(payload) != expected_size_bytes:
        raise FrozenV4ModelError("frozen V4 model size mismatch")
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise FrozenV4ModelError("frozen V4 model SHA-256 mismatch")

    current_sklearn = package_version("scikit-learn")
    if current_sklearn != FROZEN_V4_SKLEARN_VERSION:
        raise FrozenV4ModelError(
            "frozen V4 scikit-learn runtime mismatch: "
            f"required={FROZEN_V4_SKLEARN_VERSION} current={current_sklearn}"
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", InconsistentVersionWarning)
            loaded = joblib.load(io.BytesIO(payload))
    except InconsistentVersionWarning as exc:
        raise FrozenV4ModelError(
            "frozen V4 scikit-learn version mismatch: "
            f"artifact={exc.original_sklearn_version} "
            f"runtime={exc.current_sklearn_version}"
        ) from exc
    if not isinstance(loaded, dict):
        raise FrozenV4ModelError("frozen V4 model bundle must be a mapping")
    validate_frozen_v4_bundle(loaded)
    return loaded


def predict_frozen_v4_probability(
    bundle: Mapping[str, Any],
    predictors: Mapping[str, float | int | None],
) -> float:
    """Reproduce frozen XGBoost inference using the artifact's exact column order."""

    validate_frozen_v4_bundle(bundle)
    names = tuple(str(name) for name in bundle["predictor_names"])
    missing = [name for name in names if name not in predictors]
    if missing:
        raise FrozenV4ModelError(
            f"frozen V4 predictor schema missing key: {missing[0]}"
        )

    row: list[float] = []
    for name in names:
        value = predictors[name]
        if value is None:
            row.append(float("nan"))
            continue
        number = float(value)
        if not math.isfinite(number):
            raise FrozenV4ModelError(f"frozen V4 predictor not finite: {name}")
        row.append(number)

    matrix = np.asarray([row], dtype=float)
    imputed = bundle["imputer"].transform(matrix)
    probabilities = np.asarray(bundle["estimator"].predict_proba(imputed), dtype=float)
    if probabilities.shape != (1, 2):
        raise FrozenV4ModelError("frozen V4 estimator probability shape invalid")
    probability_up = float(probabilities[0, 1])
    if not math.isfinite(probability_up) or not 0.0 <= probability_up <= 1.0:
        raise FrozenV4ModelError("frozen V4 probability is invalid")

    if bundle["calibration_method"] != "identity":
        raise FrozenV4ModelError("unsupported frozen V4 calibration method")
    return probability_up
