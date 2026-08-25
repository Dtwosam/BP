from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from bp_engine.modeling.models import DatasetSplit
from bp_engine.modeling.split import equal_market_weights


@dataclass(frozen=True)
class PreparedMatrix:
    predictor_names: tuple[str, ...]
    dropped_all_missing: tuple[str, ...]
    imputer: SimpleImputer
    scaler: StandardScaler
    x_train: np.ndarray
    x_validation: np.ndarray
    x_test: np.ndarray
    x_train_scaled: np.ndarray
    x_validation_scaled: np.ndarray
    x_test_scaled: np.ndarray


@dataclass(frozen=True)
class TrainedModel:
    family: str
    config: dict[str, Any]
    estimator: Any
    validation_probabilities: tuple[float, ...]
    test_probabilities: tuple[float, ...]


def _matrix(rows, names: tuple[str, ...]) -> np.ndarray:
    return np.asarray(
        [[row.predictors.get(name) for name in names] for row in rows],
        dtype=float,
    )


def prepare_matrices(split: DatasetSplit) -> PreparedMatrix:
    if not split.train.rows:
        raise ValueError("training rows must not be empty")
    all_names = tuple(sorted(split.train.rows[0].predictors))
    for partition in (split.train, split.validation, split.test):
        for row in partition.rows:
            if tuple(sorted(row.predictors)) != all_names:
                raise ValueError("predictor schema changed across split rows")

    dropped: list[str] = []
    kept: list[str] = []
    for name in all_names:
        values = [row.predictors.get(name) for row in split.train.rows]
        if all(value is None for value in values):
            dropped.append(name)
        else:
            kept.append(name)
    if not kept:
        raise ValueError("all predictor columns are missing in training data")

    names = tuple(kept)
    x_train_raw = _matrix(split.train.rows, names)
    x_validation_raw = _matrix(split.validation.rows, names)
    x_test_raw = _matrix(split.test.rows, names)
    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(x_train_raw)
    x_validation = imputer.transform(x_validation_raw)
    x_test = imputer.transform(x_test_raw)
    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_validation_scaled = scaler.transform(x_validation)
    x_test_scaled = scaler.transform(x_test)
    return PreparedMatrix(
        predictor_names=names,
        dropped_all_missing=tuple(dropped),
        imputer=imputer,
        scaler=scaler,
        x_train=x_train,
        x_validation=x_validation,
        x_test=x_test,
        x_train_scaled=x_train_scaled,
        x_validation_scaled=x_validation_scaled,
        x_test_scaled=x_test_scaled,
    )


def _targets(rows) -> np.ndarray:
    return np.asarray([row.target for row in rows], dtype=int)


def train_logistic(split: DatasetSplit, prepared: PreparedMatrix) -> TrainedModel:
    config: dict[str, Any] = {
        "solver": "lbfgs",
        "max_iter": 1000,
        "random_state": 20260825,
    }
    estimator = LogisticRegression(**config)
    estimator.fit(
        prepared.x_train_scaled,
        _targets(split.train.rows),
        sample_weight=np.asarray(equal_market_weights(split.train.rows)),
    )
    validation = estimator.predict_proba(prepared.x_validation_scaled)[:, 1]
    test = estimator.predict_proba(prepared.x_test_scaled)[:, 1]
    return TrainedModel(
        family="logistic",
        config=config,
        estimator=estimator,
        validation_probabilities=tuple(float(value) for value in validation),
        test_probabilities=tuple(float(value) for value in test),
    )


def train_xgboost(split: DatasetSplit, prepared: PreparedMatrix) -> TrainedModel:
    config: dict[str, Any] = {
        "n_estimators": 200,
        "max_depth": 3,
        "learning_rate": 0.05,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "reg_lambda": 1.0,
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
        "random_state": 20260825,
        "n_jobs": 1,
    }
    estimator = XGBClassifier(**config)
    estimator.fit(
        prepared.x_train,
        _targets(split.train.rows),
        sample_weight=np.asarray(equal_market_weights(split.train.rows)),
        verbose=False,
    )
    validation = estimator.predict_proba(prepared.x_validation)[:, 1]
    test = estimator.predict_proba(prepared.x_test)[:, 1]
    return TrainedModel(
        family="xgboost",
        config=config,
        estimator=estimator,
        validation_probabilities=tuple(float(value) for value in validation),
        test_probabilities=tuple(float(value) for value in test),
    )
