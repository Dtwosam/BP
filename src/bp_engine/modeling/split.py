from __future__ import annotations

from collections import Counter

from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.models import (
    SPLIT_VERSION,
    DatasetSnapshot,
    DatasetSplit,
    MarketPartition,
    SupervisedRow,
)


class SplitIntegrityError(RuntimeError):
    """Raised when a chronological market split would be unsafe or degenerate."""


def equal_market_weights(rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]:
    counts = Counter(row.condition_id for row in rows)
    return tuple(1.0 / counts[row.condition_id] for row in rows)


def _partition(
    name: str, condition_ids: tuple[str, ...], rows: tuple[SupervisedRow, ...]
) -> MarketPartition:
    membership = set(condition_ids)
    selected = tuple(row for row in rows if row.condition_id in membership)
    return MarketPartition(name=name, condition_ids=condition_ids, rows=selected)


def chronological_market_split(
    dataset: DatasetSnapshot,
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    embargo_markets: int = 1,
    min_markets: int = 6,
) -> DatasetSplit:
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be in (0, 1)")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be in (0, 1)")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train_fraction + validation_fraction must be < 1")
    if embargo_markets < 0:
        raise ValueError("embargo_markets must be non-negative")

    market_starts: dict[str, object] = {}
    market_targets: dict[str, int] = {}
    for row in dataset.rows:
        existing_start = market_starts.setdefault(row.condition_id, row.market_start_at)
        if existing_start != row.market_start_at:
            raise SplitIntegrityError(f"market start changed for {row.condition_id}")
        existing_target = market_targets.setdefault(row.condition_id, row.target)
        if existing_target != row.target:
            raise SplitIntegrityError(f"target changed within market {row.condition_id}")

    ordered = tuple(
        condition_id
        for condition_id, _ in sorted(
            market_starts.items(), key=lambda item: (item[1], item[0])
        )
    )
    market_count = len(ordered)
    if market_count < min_markets:
        raise SplitIntegrityError(
            f"market count {market_count} is below minimum {min_markets}"
        )

    train_boundary = int(market_count * train_fraction)
    validation_boundary = int(
        market_count * (train_fraction + validation_fraction)
    )
    if not 0 < train_boundary < validation_boundary < market_count:
        raise SplitIntegrityError("split fractions produce an empty partition")
    if embargo_markets >= train_boundary:
        raise SplitIntegrityError("embargo consumes training partition")
    if validation_boundary - train_boundary <= embargo_markets:
        raise SplitIntegrityError("embargo consumes validation partition")

    train_end = train_boundary - embargo_markets
    validation_end = validation_boundary - embargo_markets
    train_ids = ordered[:train_end]
    first_embargo = ordered[train_end:train_boundary]
    validation_ids = ordered[train_boundary:validation_end]
    second_embargo = ordered[validation_end:validation_boundary]
    test_ids = ordered[validation_boundary:]
    embargo_ids = first_embargo + second_embargo

    if not train_ids or not validation_ids or not test_ids:
        raise SplitIntegrityError("split contains an empty partition")

    train_targets = {market_targets[condition_id] for condition_id in train_ids}
    if train_targets != {0, 1}:
        raise SplitIntegrityError("training partition must contain both target classes")

    train = _partition("train", train_ids, dataset.rows)
    validation = _partition("validation", validation_ids, dataset.rows)
    test = _partition("test", test_ids, dataset.rows)
    split_sha256 = canonical_hash(
        {
            "split_version": SPLIT_VERSION,
            "dataset_sha256": dataset.dataset_sha256,
            "train_fraction": train_fraction,
            "validation_fraction": validation_fraction,
            "embargo_markets": embargo_markets,
            "train": train_ids,
            "validation": validation_ids,
            "test": test_ids,
            "embargo": embargo_ids,
        }
    )
    return DatasetSplit(
        split_version=SPLIT_VERSION,
        dataset_sha256=dataset.dataset_sha256,
        train=train,
        validation=validation,
        test=test,
        embargo_condition_ids=embargo_ids,
        split_sha256=split_sha256,
    )
