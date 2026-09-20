from decimal import Decimal

from bp_engine.execution.models import (
    PAPER_EXECUTION_VERSION,
    V3_FROZEN_PAPER_EXECUTION_VERSION,
    PaperExecutionConfig,
)


def test_default_v1_paper_config_hash_payload_is_backward_compatible() -> None:
    config = PaperExecutionConfig()

    assert config.execution_version == PAPER_EXECUTION_VERSION
    assert config.prediction_version is None
    assert config.as_mapping() == {
        "execution_version": "paper-execution-v1",
        "starting_cash_usd": "100.00",
        "target_notional_usd": "5.00",
        "latency_ms": 250,
        "order_ttl_ms": 2000,
        "share_precision": 6,
    }


def test_v3_paper_config_is_isolated_without_changing_virtual_sizing() -> None:
    config = PaperExecutionConfig(
        execution_version=V3_FROZEN_PAPER_EXECUTION_VERSION,
        prediction_version="v3-frozen-paper-v1",
    )

    assert config.execution_version == "paper-execution-v3-frozen-v1"
    assert config.prediction_version == "v3-frozen-paper-v1"
    assert config.starting_cash_usd == Decimal("100.00")
    assert config.target_notional_usd == Decimal("5.00")
    assert config.as_mapping()["execution_version"] == "paper-execution-v3-frozen-v1"
    assert "prediction_version" not in config.as_mapping()
