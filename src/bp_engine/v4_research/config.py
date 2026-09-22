from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


V4_SHORT_CONTEXT_PREDICTORS = (
    "coinbase_return_from_market_start",
    "coinbase_return_30s",
    "coinbase_return_60s",
    "coinbase_return_120s",
    "bybit_spot_return_from_market_start",
    "bybit_spot_return_30s",
    "bybit_spot_return_60s",
    "bybit_spot_return_120s",
    "bybit_linear_return_from_market_start",
    "bybit_linear_return_30s",
    "bybit_linear_return_60s",
    "bybit_linear_return_120s",
    "coinbase_bybit_spot_return_spread",
    "coinbase_bybit_spot_direction_agree",
    "spot_linear_direction_agree",
    "bybit_linear_vs_spot_basis",
    "bybit_linear_funding_rate",
    "bybit_linear_open_interest",
    "seconds_elapsed",
    "seconds_remaining",
    "fraction_elapsed",
)

V4_REGIME_CONTEXT_PREDICTORS = (
    "coinbase_return_5m",
    "coinbase_return_15m",
    "coinbase_return_60m",
    "bybit_spot_return_5m",
    "bybit_spot_return_15m",
    "bybit_spot_return_60m",
    "bybit_linear_return_5m",
    "bybit_linear_return_15m",
    "bybit_linear_return_60m",
    "regime_5m_direction",
    "regime_15m_direction",
    "regime_60m_direction",
    "regime_5m_venue_agreement",
    "regime_15m_venue_agreement",
    "regime_60m_venue_agreement",
    "regime_trend_score",
    "regime_bull",
    "regime_bear",
    "regime_sideways_mixed",
)

V4_PREDICTOR_NAMES = (
    *V4_SHORT_CONTEXT_PREDICTORS,
    *V4_REGIME_CONTEXT_PREDICTORS,
)


@dataclass(frozen=True)
class V4GateBConfig:
    research_plan_version: str
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    feature_offsets_seconds: tuple[int, ...]
    epoch_start: datetime
    epoch_end: datetime
    train_duration: timedelta
    validation_duration: timedelta
    test_duration: timedelta
    step_duration: timedelta
    final_holdout_duration: timedelta
    embargo_markets: int
    min_train_markets: int
    min_validation_markets: int
    min_test_markets: int
    min_final_holdout_markets: int
    ordinary_fold_count: int
    known_regimes: tuple[str, ...]
    min_known_regime_markets: int
    predictor_names: tuple[str, ...]
    short_context_predictor_names: tuple[str, ...]
    forecast_candidates: tuple[str, ...]
    primary_validation_metric: str
    validation_tie_breakers: tuple[str, ...]
    nonlinear_replacement_rule: str
    calibration_candidates: tuple[str, ...]
    calibration_eligibility_rule: str
    offset_candidates_seconds: tuple[int, ...]
    fee_rate: float
    slippage_buffer: float
    min_edge_grid: tuple[float, ...]
    no_trade_candidate: bool
    max_selected_book_age_seconds: int
    min_validation_trades_per_fold: int
    required_non_negative_validation_folds: int
    require_positive_aggregate_validation_pnl: bool
    reporting_slices: tuple[str, ...]
    min_reporting_slice_markets: int
    side_specific_policy_allowed: bool
    regime_specific_policy_allowed: bool
    coverage_frontier_required: bool
    drawdown_reporting_required: bool
    losing_streak_reporting_required: bool
    profit_factor_reporting_required: bool
    execution_availability_report_required: bool

    def __post_init__(self) -> None:
        if self.epoch_start.tzinfo is None or self.epoch_end.tzinfo is None:
            raise ValueError("epoch bounds must be timezone-aware")
        if self.epoch_end <= self.epoch_start:
            raise ValueError("epoch_end must be after epoch_start")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if tuple(sorted(set(self.feature_offsets_seconds))) != (
            self.feature_offsets_seconds
        ):
            raise ValueError("feature_offsets_seconds must be sorted and unique")
        if any(
            offset <= 0 or offset >= self.horizon_seconds
            for offset in self.feature_offsets_seconds
        ):
            raise ValueError("feature offsets must be within the market horizon")
        for name in (
            "train_duration",
            "validation_duration",
            "test_duration",
            "step_duration",
            "final_holdout_duration",
        ):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.step_duration < self.test_duration:
            raise ValueError("step_duration must be at least test_duration")
        if self.embargo_markets < 0:
            raise ValueError("embargo_markets must be non-negative")
        for name in (
            "min_train_markets",
            "min_validation_markets",
            "min_test_markets",
            "min_final_holdout_markets",
            "ordinary_fold_count",
            "min_known_regime_markets",
            "max_selected_book_age_seconds",
            "min_validation_trades_per_fold",
            "required_non_negative_validation_folds",
            "min_reporting_slice_markets",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

        ordinary_span = (
            self.epoch_end - self.epoch_start - self.final_holdout_duration
        )
        first_fold_span = (
            self.train_duration + self.validation_duration + self.test_duration
        )
        if ordinary_span < first_fold_span:
            raise ValueError("epoch is too short for one ordinary fold")
        calculated_fold_count = (
            (ordinary_span - first_fold_span) // self.step_duration
        ) + 1
        if calculated_fold_count != self.ordinary_fold_count:
            raise ValueError("ordinary_fold_count does not match frozen geometry")

        for name in (
            "known_regimes",
            "predictor_names",
            "short_context_predictor_names",
            "forecast_candidates",
            "validation_tie_breakers",
            "calibration_candidates",
            "reporting_slices",
        ):
            values = getattr(self, name)
            if not values or any(not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")

        if not set(self.short_context_predictor_names).issubset(
            self.predictor_names
        ):
            raise ValueError("short-context predictors must be V4 predictors")
        for name in (
            "primary_validation_metric",
            "nonlinear_replacement_rule",
            "calibration_eligibility_rule",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.offset_candidates_seconds != self.feature_offsets_seconds:
            raise ValueError("offset candidates must equal frozen feature offsets")
        if self.fee_rate < 0 or self.slippage_buffer < 0:
            raise ValueError("execution costs must be non-negative")
        if not self.min_edge_grid:
            raise ValueError("min_edge_grid must be non-empty")
        if tuple(sorted(set(self.min_edge_grid))) != self.min_edge_grid:
            raise ValueError("min_edge_grid must be sorted and unique")
        if any(value < 0 for value in self.min_edge_grid):
            raise ValueError("min_edge_grid must be non-negative")
        if self.required_non_negative_validation_folds > self.ordinary_fold_count:
            raise ValueError(
                "required_non_negative_validation_folds cannot exceed ordinary folds"
            )
        if self.side_specific_policy_allowed:
            raise ValueError("V4 preregistration forbids side-specific policy tuning")
        if self.regime_specific_policy_allowed:
            raise ValueError("V4 preregistration forbids regime-specific policy tuning")


def v4_gate_b_config_payload(config: V4GateBConfig) -> dict[str, object]:
    return {
        "research_plan_version": config.research_plan_version,
        "dataset_version": config.dataset_version,
        "feature_version": config.feature_version,
        "label_version": config.label_version,
        "horizon_seconds": config.horizon_seconds,
        "feature_offsets_seconds": config.feature_offsets_seconds,
        "epoch_start": config.epoch_start,
        "epoch_end": config.epoch_end,
        "train_duration_seconds": config.train_duration.total_seconds(),
        "validation_duration_seconds": config.validation_duration.total_seconds(),
        "test_duration_seconds": config.test_duration.total_seconds(),
        "step_duration_seconds": config.step_duration.total_seconds(),
        "final_holdout_duration_seconds": (
            config.final_holdout_duration.total_seconds()
        ),
        "embargo_markets": config.embargo_markets,
        "min_train_markets": config.min_train_markets,
        "min_validation_markets": config.min_validation_markets,
        "min_test_markets": config.min_test_markets,
        "min_final_holdout_markets": config.min_final_holdout_markets,
        "ordinary_fold_count": config.ordinary_fold_count,
        "known_regimes": config.known_regimes,
        "min_known_regime_markets": config.min_known_regime_markets,
        "predictor_names": config.predictor_names,
        "short_context_predictor_names": config.short_context_predictor_names,
        "forecast_candidates": config.forecast_candidates,
        "primary_validation_metric": config.primary_validation_metric,
        "validation_tie_breakers": config.validation_tie_breakers,
        "nonlinear_replacement_rule": config.nonlinear_replacement_rule,
        "calibration_candidates": config.calibration_candidates,
        "calibration_eligibility_rule": config.calibration_eligibility_rule,
        "offset_candidates_seconds": config.offset_candidates_seconds,
        "fee_rate": config.fee_rate,
        "slippage_buffer": config.slippage_buffer,
        "min_edge_grid": config.min_edge_grid,
        "no_trade_candidate": config.no_trade_candidate,
        "max_selected_book_age_seconds": config.max_selected_book_age_seconds,
        "min_validation_trades_per_fold": config.min_validation_trades_per_fold,
        "required_non_negative_validation_folds": (
            config.required_non_negative_validation_folds
        ),
        "require_positive_aggregate_validation_pnl": (
            config.require_positive_aggregate_validation_pnl
        ),
        "reporting_slices": config.reporting_slices,
        "min_reporting_slice_markets": config.min_reporting_slice_markets,
        "side_specific_policy_allowed": config.side_specific_policy_allowed,
        "regime_specific_policy_allowed": config.regime_specific_policy_allowed,
        "coverage_frontier_required": config.coverage_frontier_required,
        "drawdown_reporting_required": config.drawdown_reporting_required,
        "losing_streak_reporting_required": config.losing_streak_reporting_required,
        "profit_factor_reporting_required": config.profit_factor_reporting_required,
        "execution_availability_report_required": (
            config.execution_availability_report_required
        ),
    }


FROZEN_V4_GATE_B_CONFIG = V4GateBConfig(
    research_plan_version="v4-gate-b-preregister-v1",
    dataset_version="supervised-core-v4-regime-aware-v1",
    feature_version="core-v4-regime-aware",
    label_version="official-outcome-v1",
    horizon_seconds=300,
    feature_offsets_seconds=(60, 120, 180, 240),
    epoch_start=datetime(2026, 9, 23, 0, 0, tzinfo=UTC),
    epoch_end=datetime(2026, 9, 30, 0, 0, tzinfo=UTC),
    train_duration=timedelta(hours=48),
    validation_duration=timedelta(hours=12),
    test_duration=timedelta(hours=12),
    step_duration=timedelta(hours=12),
    final_holdout_duration=timedelta(hours=24),
    embargo_markets=1,
    min_train_markets=480,
    min_validation_markets=120,
    min_test_markets=120,
    min_final_holdout_markets=240,
    ordinary_fold_count=7,
    known_regimes=("bull", "bear", "sideways_mixed"),
    min_known_regime_markets=120,
    predictor_names=V4_PREDICTOR_NAMES,
    short_context_predictor_names=V4_SHORT_CONTEXT_PREDICTORS,
    forecast_candidates=(
        "training_prior",
        "single_feature_btc_logistic",
        "short_context_v4_logistic",
        "full_v4_logistic",
        "full_v4_xgboost",
    ),
    primary_validation_metric="mean_fold_log_loss",
    validation_tie_breakers=(
        "mean_fold_brier_score",
        "worst_known_regime_log_loss",
        "simpler_model",
    ),
    nonlinear_replacement_rule=(
        "strictly_better_overall_validation_log_loss_and_brier_than_full_v4_logistic_"
        "and_not_worse_on_both_metrics_in_any_evaluable_known_regime"
    ),
    calibration_candidates=("identity", "platt"),
    calibration_eligibility_rule=(
        "improve_overall_validation_log_loss_and_brier_without_negative_coefficient_"
        "and_not_worse_on_both_metrics_in_any_evaluable_known_regime"
    ),
    offset_candidates_seconds=(60, 120, 180, 240),
    fee_rate=0.07,
    slippage_buffer=0.01,
    min_edge_grid=(0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15),
    no_trade_candidate=True,
    max_selected_book_age_seconds=10,
    min_validation_trades_per_fold=16,
    required_non_negative_validation_folds=6,
    require_positive_aggregate_validation_pnl=True,
    reporting_slices=(
        "overall",
        "bull",
        "bear",
        "sideways_mixed",
        "unknown",
        "up",
        "down",
        "regime_by_side",
    ),
    min_reporting_slice_markets=60,
    side_specific_policy_allowed=False,
    regime_specific_policy_allowed=False,
    coverage_frontier_required=True,
    drawdown_reporting_required=True,
    losing_streak_reporting_required=True,
    profit_factor_reporting_required=True,
    execution_availability_report_required=True,
)
