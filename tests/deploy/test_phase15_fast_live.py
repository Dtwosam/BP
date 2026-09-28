from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_UNIT = ROOT / "deploy" / "bp-phase15-fast-live-source.service"
RECEIVER_UNIT = ROOT / "deploy" / "bp-phase15-fast-live-receiver.service"
REQUIREMENTS = ROOT / "deploy" / "phase15-fast-live-executor-requirements.txt"
SOURCE = ROOT / "scripts" / "run_phase15_v3_fast_live_source.py"
RECEIVER = ROOT / "scripts" / "run_phase15_v3_fast_live_receiver.py"
EXECUTOR = ROOT / "src" / "bp_engine" / "execution" / "fast_live_executor.py"


def test_fast_live_runtime_dependencies_are_pinned() -> None:
    assert REQUIREMENTS.read_text(encoding="utf-8").splitlines() == [
        "polymarket-client==0.7.1",
        "google-cloud-pubsub==2.41.0",
        "websockets==15.0.1",
    ]


def test_fast_live_source_has_no_wallet_or_live_money_runtime() -> None:
    text = SOURCE_UNIT.read_text(encoding="utf-8")
    for marker in (
        "MODE=research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "UnsetEnvironment=POLYMARKET_PRIVATE_KEY POLYMARKET_WALLET_ADDRESS",
        "BP_PHASE15_FAST_LIVE_SOURCE_ENABLED=yes",
        "--expected-main ${BP_FAST_LIVE_EXPECTED_MAIN}",
        "--topic-id ${BP_FAST_LIVE_TOPIC_ID}",
    ):
        assert marker in text
    source = SOURCE.read_text(encoding="utf-8")
    assert "PublisherClient()" in source
    assert 'default=0.05' in source
    assert "POLYMARKET_PRIVATE_KEY" in source
    assert "must not be present in fast live source" in source
    assert "Telegram" not in source


def test_fast_live_receiver_is_preauthorized_one_shot_and_fail_closed() -> None:
    unit = RECEIVER_UNIT.read_text(encoding="utf-8")
    for marker in (
        "ConditionPathExists=/etc/bp-fast-live/authorization.json",
        "ConditionPathExists=/etc/bp-canary/live.env",
        "BP_PHASE15_FAST_LIVE_EXECUTOR_ENABLED=yes",
        "--expected-main ${BP_FAST_LIVE_EXPECTED_MAIN}",
        "--subscription-id ${BP_FAST_LIVE_SUBSCRIPTION_ID}",
        "fast-live-service-stop > /etc/bp-fast-live/KILL",
    ):
        assert marker in unit
    receiver = RECEIVER.read_text(encoding="utf-8")
    assert "SubscriberClient()" in receiver
    assert "StreamingBookCache()" in receiver
    assert "SafetyRefresher" in receiver
    assert "verify_runtime_authorization" in receiver
    assert "verify_envelope" in receiver
    assert "executor.execute(verified)" in receiver


def test_fast_live_executor_quotes_before_attempt_and_post() -> None:
    text = EXECUTOR.read_text(encoding="utf-8")
    sign = text.index("create_limit_order")
    quote = text.index("get_order_book", sign)
    marketability = text.index("marketable_depth", quote)
    attempt = text.index("_write_exclusive_json(self.attempt_path", marketability)
    post = text.index("post_order", attempt)
    assert sign < quote < marketability < attempt < post
    assert "time.sleep(float(self._order_ttl_seconds))" in text
    assert "fast-live-one-shot-attempt-consumed" in text
