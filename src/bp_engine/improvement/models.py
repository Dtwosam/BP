from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from bp_engine.improvement.hashing import derive_id, semantic_sha256

EXPERIMENT_VERSION = "improvement-experiment-v1"
EVALUATION_VERSION = "improvement-evaluation-v1"
DECISION_VERSION = "improvement-decision-v1"


class ChangeFamily(StrEnum):
    FEATURE = "feature"
    MODEL = "model"
    CALIBRATION = "calibration"
    TIMING = "timing"
    ABSTENTION = "abstention"
    COST_ASSUMPTION = "cost_assumption"


class EvidenceRole(StrEnum):
    DEVELOPMENT_TRAIN = "development_train"
    DEVELOPMENT_VALIDATION = "development_validation"
    ORDINARY_OOS = "ordinary_oos"
    FRESH_HOLDOUT = "fresh_holdout"
    PROSPECTIVE_PAPER = "prospective_paper"


class PromotionDecision(StrEnum):
    REJECT_CHALLENGER = "reject_challenger"
    KEEP_CHAMPION = "keep_champion"
    PROMOTE_CHALLENGER = "promote_challenger"


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")


@dataclass(frozen=True)
class ChampionRef:
    calibration_run_id: str
    calibration_semantic_sha256: str
    backtest_run_id: str
    backtest_semantic_sha256: str
    training_run_id: str
    training_semantic_sha256: str

    def __post_init__(self) -> None:
        for name in ("calibration_run_id", "backtest_run_id", "training_run_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        for name in (
            "calibration_semantic_sha256",
            "backtest_semantic_sha256",
            "training_semantic_sha256",
        ):
            _require_sha256(name, getattr(self, name))


@dataclass(frozen=True)
class ImprovementExperimentSpec:
    experiment_id: str
    experiment_version: str
    hypothesis: str
    horizon_seconds: int
    change_family: ChangeFamily
    champion: ChampionRef
    challenger: dict[str, Any]
    source_versions: dict[str, str]
    research_start: datetime
    research_end: datetime
    selection_policy: dict[str, Any]
    confirmation_policy: dict[str, Any]
    cost_assumptions: dict[str, Any]
    primary_metric: str
    guardrail_metrics: tuple[str, ...]
    legacy_confirmation_identifiers: tuple[str, ...]
    semantic_sha256: str
    created_at: datetime

    @classmethod
    def build(
        cls,
        *,
        experiment_version: str,
        hypothesis: str,
        horizon_seconds: int,
        change_family: ChangeFamily,
        champion: ChampionRef,
        challenger: dict[str, Any],
        source_versions: dict[str, str],
        research_start: datetime,
        research_end: datetime,
        selection_policy: dict[str, Any],
        confirmation_policy: dict[str, Any],
        cost_assumptions: dict[str, Any],
        primary_metric: str,
        guardrail_metrics: tuple[str, ...],
        legacy_confirmation_identifiers: tuple[str, ...],
        created_at: datetime,
    ) -> ImprovementExperimentSpec:
        if experiment_version != EXPERIMENT_VERSION:
            raise ValueError(f"experiment_version must be {EXPERIMENT_VERSION}")
        normalized_hypothesis = hypothesis.strip()
        if not normalized_hypothesis:
            raise ValueError("hypothesis must not be blank")
        if horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        _require_aware("research_start", research_start)
        _require_aware("research_end", research_end)
        _require_aware("created_at", created_at)
        if research_end <= research_start:
            raise ValueError("research_start must be before research_end")
        if not primary_metric.strip():
            raise ValueError("primary_metric must not be blank")
        if not guardrail_metrics:
            raise ValueError("guardrail_metrics must not be empty")
        if any(not metric.strip() for metric in guardrail_metrics):
            raise ValueError("guardrail_metrics must not contain blank values")

        semantic_payload = {
            "experiment_version": experiment_version,
            "hypothesis": normalized_hypothesis,
            "horizon_seconds": horizon_seconds,
            "change_family": change_family,
            "champion": champion,
            "challenger": dict(challenger),
            "source_versions": dict(source_versions),
            "research_start": research_start,
            "research_end": research_end,
            "selection_policy": dict(selection_policy),
            "confirmation_policy": dict(confirmation_policy),
            "cost_assumptions": dict(cost_assumptions),
            "primary_metric": primary_metric.strip(),
            "guardrail_metrics": tuple(guardrail_metrics),
            "legacy_confirmation_identifiers": tuple(
                sorted(set(legacy_confirmation_identifiers))
            ),
        }
        digest = semantic_sha256(semantic_payload)
        return cls(
            experiment_id=derive_id("phase13-exp", digest),
            experiment_version=experiment_version,
            hypothesis=normalized_hypothesis,
            horizon_seconds=horizon_seconds,
            change_family=change_family,
            champion=champion,
            challenger=dict(challenger),
            source_versions=dict(source_versions),
            research_start=research_start,
            research_end=research_end,
            selection_policy=dict(selection_policy),
            confirmation_policy=dict(confirmation_policy),
            cost_assumptions=dict(cost_assumptions),
            primary_metric=primary_metric.strip(),
            guardrail_metrics=tuple(guardrail_metrics),
            legacy_confirmation_identifiers=tuple(
                sorted(set(legacy_confirmation_identifiers))
            ),
            semantic_sha256=digest,
            created_at=created_at,
        )
