from pathlib import Path

ROOT = Path(__file__).parents[2]
HOST = ROOT / "scripts" / "deploy" / "phase10_host_acceptance.sh"
CLOUD = ROOT / "scripts" / "deploy" / "phase10_cloudshell_accept.sh"
RUNBOOK = ROOT / "docs" / "PHASE-10-DEPLOYMENT.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
SERVICE = ROOT / "deploy" / "bp-live-predictor.service"

SOURCE_5M = "phase9-300-c9f0e00eb7836af08008c66909f8f179"
SOURCE_15M = "phase9-900-15c234f25588b23cce73a12f87a2e2ea"
SOURCE_SEMANTIC_5M = (
    "c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24"
)
SOURCE_SEMANTIC_15M = (
    "15c234f25588b23cce73a12f87a2e2ea9087490055f203f22f183594b4bcfacd"
)


def _required_text(path: Path) -> str:
    assert path.exists(), f"missing required Phase 10 deployment asset: {path}"
    return path.read_text(encoding="utf-8")


def test_phase10_host_acceptance_is_exact_sha_money_disabled_and_fail_closed() -> None:
    text = _required_text(HOST)

    required = (
        "BP_VERIFIED_HEAD",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "TRADING_MODE",
        "research",
        "00010_live_predictions.sql".replace("00010", "0010"),
        SOURCE_5M,
        SOURCE_15M,
        SOURCE_SEMANTIC_5M,
        SOURCE_SEMANTIC_15M,
        "RECORDER_BEFORE",
        "RECORDER_AFTER",
        "DISK_STATUS_BEFORE",
        "DISK_STATUS_AFTER",
        "bp-live-predictor",
        "User=bp",
        "PREDICTION_COUNT_5M",
        "PREDICTION_COUNT_15M",
        "LATE_OR_MISSED_COVERAGE",
        "MAX_LATENESS_MS",
        "PRE_OUTCOME_VIOLATIONS",
        "SOURCE_CUTOFF_VIOLATIONS",
        "SEMANTIC_HASH_VIOLATIONS",
        "DUPLICATE_NATURAL_KEYS",
        "EVALUATION_MUTATION_VIOLATIONS",
        "ORDER_SIDE_EFFECT_VIOLATIONS",
        "VERDICT=PASS",
        "PHASE10_HOST_ACCEPTANCE=PASS",
    )
    for token in required:
        assert token in text

    lowered = text.lower()
    for forbidden in (
        "safe.directory",
        "place_order",
        "private_key",
        "private-key",
        "wallet_address",
        "synthetic_fill",
        "synthetic fill",
    ):
        assert forbidden not in lowered


def test_phase10_host_acceptance_is_prospective_and_never_backfills_misses() -> None:
    text = _required_text(HOST)

    required = (
        "future",
        "scheduled_at",
        "recorded_at",
        "market_end_at",
        "source_observed_at",
        "market_probability_observed_at",
        "up_book_cutoff_at",
        "down_book_cutoff_at",
        "max_lateness",
        "prediction-report",
        "evaluation",
        "pending",
    )
    lowered = text.lower()
    for token in required:
        assert token.lower() in lowered

    for forbidden in (
        "backfill prediction",
        "historical replay",
        "insert into live_predictions",
        "update live_predictions",
    ):
        assert forbidden not in lowered


def test_phase10_systemd_unit_is_unprivileged_and_research_only() -> None:
    text = _required_text(SERVICE)

    required = (
        "User=bp",
        "Group=bp",
        "NoNewPrivileges=true",
        "ProtectSystem=full",
        "python -m bp_engine.live_prediction run",
        SOURCE_5M,
        SOURCE_15M,
    )
    for token in required:
        assert token in text

    lowered = text.lower()
    assert "docker.sock" not in lowered
    assert "privileged=true" not in lowered


def test_phase10_cloudshell_uses_verified_archive_into_bp_owned_source() -> None:
    text = _required_text(CLOUD)

    required = (
        "PHASE10_HEAD",
        "build/phase-10-live-prediction-engine",
        "git -C /opt/bp worktree add --detach",
        "WORKTREE_HEAD",
        r'git -C \"\$WT\" archive --format=tar',
        r'sudo -u bp tar -xf - -C \"\$SRC\"',
        r'BP_REPO=\"\$SRC\"',
        r'BP_VERIFIED_HEAD=\"\$WORKTREE_HEAD\"',
        "phase10_host_acceptance.sh",
        "phase10-host-acceptance-latest.log",
        "PHASE10_HOST_ACCEPTANCE=PASS",
    )
    for token in required:
        assert token in text

    lowered = text.lower()
    assert "chown" not in lowered
    assert "safe.directory" not in lowered


def test_phase10_acceptance_runtime_paths_survive_private_tmp() -> None:
    host = _required_text(HOST)
    cloud = _required_text(CLOUD)

    assert "PrivateTmp=true" in host
    assert 'VENV="/var/tmp/' not in host
    assert "/var/tmp/bp-phase10-src-" not in cloud


def test_phase10_runbook_documents_prospective_evidence_and_non_economic_gate() -> None:
    text = _required_text(RUNBOOK).lower()

    required = (
        SOURCE_5M.lower(),
        SOURCE_15M.lower(),
        SOURCE_SEMANTIC_5M.lower(),
        SOURCE_SEMANTIC_15M.lower(),
        "prospective",
        "future verified markets",
        "honest misses",
        "evaluation pending",
        "not a profitability claim",
        "trade=true",
        "research decision only",
        "phase10-host-acceptance-latest.log",
        "live trading disabled",
        "no order",
        "bp_verified_head",
    )
    for token in required:
        assert token in text


def test_ci_validates_phase10_shell_syntax() -> None:
    text = _required_text(CI)

    assert "bash -n scripts/deploy/phase10_host_acceptance.sh" in text
    assert "bash -n scripts/deploy/phase10_cloudshell_accept.sh" in text
