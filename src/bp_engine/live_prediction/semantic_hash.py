from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from itertools import product
from typing import Any

from bp_engine.features.hashing import canonical_hash
from bp_engine.live_prediction.repository import _ledger_numeric_equal

_LEDGER_QUANTUM = Decimal("0.000000000000000001")
_MAX_FLOAT_NEIGHBORS = 64
_MAX_HASH_COMBINATIONS = 4096


def _ledger_float_candidates(stored: Any) -> tuple[float, ...] | None:
    """Return every nearby IEEE float that maps to one exact ledger decimal.

    The search is deliberately bounded. If the NUMERIC(38,18) bucket contains more
    representable floats than the bound can enumerate, verification fails closed.
    """
    if stored is None or isinstance(stored, bool):
        return None
    try:
        numeric = stored if isinstance(stored, Decimal) else Decimal(str(stored))
        if not numeric.is_finite():
            return None
        target = numeric.quantize(_LEDGER_QUANTUM)
        center = float(target)
    except (ArithmeticError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(center) or not _ledger_numeric_equal(stored, center):
        return None

    candidates = {center}
    for direction in (-math.inf, math.inf):
        current = center
        exhausted_bound = True
        for _ in range(_MAX_FLOAT_NEIGHBORS):
            candidate = math.nextafter(current, direction)
            if candidate == current or not math.isfinite(candidate):
                exhausted_bound = False
                break
            if not _ledger_numeric_equal(stored, candidate):
                exhausted_bound = False
                break
            candidates.add(candidate)
            current = candidate
        if exhausted_bound:
            candidate = math.nextafter(current, direction)
            if math.isfinite(candidate) and _ledger_numeric_equal(stored, candidate):
                return None

    return tuple(sorted(candidates))


def _candidate_group(
    row: Mapping[str, Any],
    names: tuple[str, ...],
    *,
    source_name: str,
) -> tuple[tuple[str, ...], tuple[float, ...]] | None:
    source = row[source_name]
    candidates = _ledger_float_candidates(source)
    if not candidates:
        return None
    if any(not _ledger_numeric_equal(row[name], source) for name in names):
        return None
    return names, candidates


def prediction_storage_hash_matches(
    row: Mapping[str, Any],
    recovered_values: Mapping[str, Any],
) -> bool:
    """Verify a V1 prediction hash across exact Postgres NUMERIC quantization.

    Recovery never uses a tolerance. It enumerates only IEEE floats that map to the
    exact stored ledger quantum, then requires one candidate payload to reproduce the
    immutable SHA-256 exactly. A changed ledger quantum therefore cannot be accepted
    unless it also recreates the original cryptographic digest.
    """
    decision = row.get("edge_decision")
    if not isinstance(decision, Mapping):
        return False
    side = decision.get("side")
    if side not in {"up", "down"}:
        return False

    values = dict(recovered_values)
    for field_suffix, decision_name in (("best_bid", "bid"), ("best_ask", "ask")):
        field_name = f"{side}_{field_suffix}"
        original = decision.get(decision_name)
        if not _ledger_numeric_equal(row[field_name], original):
            return False
        values[field_name] = original

    groups: list[tuple[tuple[str, ...], tuple[float, ...]]] = []
    if bool(row["market_probability_observed"]):
        market_group = _candidate_group(
            row,
            ("market_probability", "raw_probability"),
            source_name="market_probability",
        )
        training_group = _candidate_group(
            row,
            ("training_prior",),
            source_name="training_prior",
        )
        if market_group is None or training_group is None:
            return False
        groups.extend((market_group, training_group))
    else:
        if row["market_probability"] is not None:
            return False
        prior_group = _candidate_group(
            row,
            ("training_prior", "raw_probability"),
            source_name="training_prior",
        )
        if prior_group is None:
            return False
        groups.append(prior_group)

    other_side = "down" if side == "up" else "up"
    for suffix in ("best_bid", "best_ask"):
        field_name = f"{other_side}_{suffix}"
        if row[field_name] is None:
            values[field_name] = None
            continue
        quote_group = _candidate_group(
            row,
            (field_name,),
            source_name=field_name,
        )
        if quote_group is None:
            return False
        groups.append(quote_group)

    combination_count = math.prod(len(candidates) for _, candidates in groups)
    if combination_count <= 0 or combination_count > _MAX_HASH_COMBINATIONS:
        return False

    target_hash = row["semantic_sha256"]
    for choices in product(*(candidates for _, candidates in groups)):
        candidate_values = dict(values)
        for (names, _), choice in zip(groups, choices, strict=True):
            for name in names:
                candidate_values[name] = choice
        if canonical_hash(candidate_values) == target_hash:
            return True
    return False
