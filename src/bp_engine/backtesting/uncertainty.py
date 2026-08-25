from __future__ import annotations

import math


def wilson_accuracy_interval(
    correct: int,
    total: int,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("total must be positive")
    if correct < 0 or correct > total:
        raise ValueError("correct must be between 0 and total")
    if not math.isfinite(z) or z <= 0:
        raise ValueError("z must be positive and finite")

    proportion = correct / total
    z_squared = z * z
    denominator = 1.0 + z_squared / total
    center = (proportion + z_squared / (2.0 * total)) / denominator
    half_width = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / total
            + z_squared / (4.0 * total * total)
        )
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)
