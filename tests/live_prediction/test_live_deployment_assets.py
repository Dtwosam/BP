from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_live_prediction_runtime_assets_exist() -> None:
    required = (
        ROOT / "src/bp_engine/live_prediction/__main__.py",
        ROOT / "src/bp_engine/live_prediction/cli.py",
        ROOT / "scripts/run_live_prediction.py",
        ROOT / "scripts/report_live_predictions.py",
        ROOT / "deploy/bp-live-predictor.service",
    )
    for path in required:
        assert path.is_file(), path


def test_module_entrypoint_delegates_to_live_prediction_cli() -> None:
    content = (ROOT / "src/bp_engine/live_prediction/__main__.py").read_text()
    assert "from bp_engine.live_prediction.cli import main" in content
    assert "main()" in content


def test_runner_scripts_are_thin_safe_cli_adapters() -> None:
    run_content = (ROOT / "scripts/run_live_prediction.py").read_text()
    report_content = (ROOT / "scripts/report_live_predictions.py").read_text()

    assert "bp_engine.live_prediction.cli" in run_content
    assert '"run"' in run_content
    assert "bp_engine.live_prediction.cli" in report_content
    assert '"report"' in report_content

    rendered = (run_content + report_content).lower()
    for forbidden in ("wallet", "private_key", "private-key", "place_order", "signing"):
        assert forbidden not in rendered


def test_systemd_unit_is_unprivileged_restartable_and_research_scoped() -> None:
    content = (ROOT / "deploy/bp-live-predictor.service").read_text()

    assert "User=bp" in content
    assert "Group=bp" in content
    assert "WorkingDirectory=/opt/bp" in content
    assert "EnvironmentFile=/etc/bp/bp.env" in content
    assert "ExecStart=/opt/bp/.venv/bin/python -m bp_engine.live_prediction run" in content
    assert "Restart=always" in content
    assert "NoNewPrivileges=true" in content
    assert "ProtectSystem=full" in content
    assert "Requires=bp-postgres.service" in content
    assert "After=bp-postgres.service" in content

    lowered = content.lower()
    assert "docker.sock" not in lowered
    assert "privileged" not in lowered
    assert "wallet" not in lowered
    assert "order" not in lowered


def test_ci_validates_new_python_runtime_assets() -> None:
    content = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "py_compile" in content
    assert "scripts/run_live_prediction.py" in content
    assert "scripts/report_live_predictions.py" in content
