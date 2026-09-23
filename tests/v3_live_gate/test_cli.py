from __future__ import annotations

import json

from bp_engine.v3_live_gate.cli import _authorization, build_parser


def test_cli_loads_recorded_user_authorization(tmp_path) -> None:
    state = tmp_path / "PROJECT_STATE.json"
    state.write_text(json.dumps({"phase_14_v3_live_gate_reassessment": {"explicit_user_live_authorization": "pass"}}))
    assert _authorization(state) is True
    args = build_parser().parse_args(["--project-state", str(state), "--bootstrap-resamples", "500"])
    assert args.bootstrap_resamples == 500
