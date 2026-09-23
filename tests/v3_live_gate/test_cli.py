from __future__ import annotations

import inspect
import json

from bp_engine.v3_live_gate.cli import (
    _authorization,
    build_database_report,
    build_parser,
)


def test_cli_loads_recorded_user_authorization(tmp_path) -> None:
    state = tmp_path / "PROJECT_STATE.json"
    state.write_text(
        json.dumps(
            {
                "phase_14_v3_live_gate_reassessment": {
                    "explicit_user_live_authorization": "pass"
                }
            }
        )
    )
    assert _authorization(state) is True
    args = build_parser().parse_args(
        [
            "--project-state",
            str(state),
            "--bootstrap-resamples",
            "500",
        ]
    )
    assert args.bootstrap_resamples == 500


def test_database_report_scopes_calibration_to_all_frozen_v3_predictions() -> None:
    source = inspect.getsource(build_database_report)
    assert "v3_prediction_ids" in source
    assert "schema.live_predictions.c.prediction_version" in source
    assert "== V3_PAPER_PREDICTION_VERSION" in source
    assert 'prediction_ids = tuple(str(row["prediction_id"]) for row in orders)' not in source
