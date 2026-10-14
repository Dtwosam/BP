"""Source-pinned Stage B1 operator feasibility REVIEW ONLY, never authorization.

Read exactly five fixed local repository files; no imports of recorder
runtime, no network, Git CLI, subprocess, database or systemd actions.
The repository source facts are pinned to the exact previously reviewed
Git blobs and must be reviewed again if any of them drift. A match is
still NO GO: Git source is not the verified live host runtime.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 65536
FILE_GIT_BLOBS = {
    "deploy/systemd/bp-recorder.service": "036bfb84d0f4f9cfd7bf5114d8e01966a086dc1b",
    "src/bp_engine/recorder/__main__.py": "4b6c3bba47d4743c7453a29a6c3ba47e28f34353",
    "src/bp_engine/recorder/service.py": "ef5de19c8efb39cf874f5620a0b9098c07824475",
    "src/bp_engine/recorder/writer.py": "1abb496199bf99b1270dbfaf97748689c54e2177",
    "src/bp_engine/recorder/stage_b1_one_shot.py": "cae616873e83a56d3a12aaae50a4b8825ebad999",
}
CHECK_NAMES = (
    "exact_five_source_blobs_verified",
    "normal_recorder_entrypoint_no_b1_injection",
    "repo_unit_restarts_recorder_always",
    "repo_unit_has_60_second_stop_grace",
    "repo_unit_lacks_approved_independent_b1_watchdog",
    "database_sink_uses_asyncio_to_thread",
    "normal_recorder_builder_creates_db_and_schema",
    "one_shot_remains_separate_dormant_library",
)


def _read_pinned_file(root: Path, relative_path: str, expected_blob: str) -> str | None:
    path = root / relative_path
    if path.is_symlink():
        return None
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return None
        blob = hashlib.sha1(
            b"blob " + str(len(raw)).encode("ascii") + b"\\x00" + raw
        ).hexdigest()
        if blob != expected_blob:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def review_source_feasibility(*, root: Path = ROOT) -> dict[str, object]:
    """Review fixed tracked-source facts, never current live-host state."""
    sources = {
        name: _read_pinned_file(root, name, digest)
        for name, digest in FILE_GIT_BLOBS.items()
    }
    exact = all(value is not None for value in sources.values())

    if exact:
        unit = sources["deploy/systemd/bp-recorder.service"]
        entrypoint = sources["src/bp_engine/recorder/__main__.py"]
        service = sources["src/bp_engine/recorder/service.py"]
        writer = sources["src/bp_engine/recorder/writer.py"]
        one_shot = sources["src/bp_engine/recorder/stage_b1_one_shot.py"]
        assert all(isinstance(x, str) for x in (unit, entrypoint, service, writer, one_shot))
        checks = {
            "exact_five_source_blobs_verified": True,
            "normal_recorder_entrypoint_no_b1_injection": (
                "service = service_factory(settings)" in entrypoint
                and "service_factory(settings, stage_b1_observer" not in entrypoint
                and "run_offline_rehearsal" not in entrypoint
                and "StageB1BulkObserver" not in entrypoint
            ),
            "repo_unit_restarts_recorder_always": (
                "\\nRestart=always\\n" in unit
                and "ExecStart=/opt/bp/.venv/bin/python -m bp_engine.recorder" in unit
            ),
            "repo_unit_has_60_second_stop_grace": (
                "\\nTimeoutStopSec=60\\n" in unit
                and "\\nKillSignal=SIGTERM\\n" in unit
            ),
            "repo_unit_lacks_approved_independent_b1_watchdog": (
                "RuntimeMaxSec=" not in unit
                and "ExecStopPost=" not in unit
                and "StageB1" not in unit
            ),
            "database_sink_uses_asyncio_to_thread": (
                "class _DatabaseSink:" in service
                and service.count("await asyncio.to_thread(write)") == 3
            ),
            "normal_recorder_builder_creates_db_and_schema": (
                "engine = create_engine(settings.database_url)" in service
                and "metadata.create_all(engine)" in service
                and "def build_default_recorder_service(" in service
            ),
            "one_shot_remains_separate_dormant_library": (
                "async def run_offline_rehearsal(" in one_shot
                and "Not a real-process kill switch." in one_shot
                and "await asyncio.gather(*workers)" in writer
            ),
        }
    else:
        checks = dict.fromkeys(CHECK_NAMES, False)

    if tuple(checks) != CHECK_NAMES:
        raise AssertionError("fixed source review schema mismatch")
    matched = all(checks.values())
    return {
        "report": "phase14_b1_source_pinned_operator_feasibility_v1",
        "status": (
            "PINNED_SOURCE_REVIEW_ONLY_NO_GO"
            if matched else "PINNED_SOURCE_DRIFT_OR_MISMATCH_NO_GO"
        ),
        "checks": checks,
        "missing_or_mismatched_checks": [name for name in CHECK_NAMES if not checks[name]],
        "file_count": len(FILE_GIT_BLOBS),
        "on_host_identity_verified": False,
        "real_systemd_restoration_verified": False,
        "db_thread_quiescence_verified": False,
        "safe_existing_recorder_restart_candidate_available": False,
        "safe_parallel_full_recorder_candidate_available": False,
        "production_execution_authorized": False,
        "proposed_operator_implementation_approved": False,
        "limitations": [
            "Pinned Git blobs are reviewed source only; deployed host bytes are unverified.",
            "Restart=always and 60-second stop grace are not an external B1 lifetime bound.",
            "Normal recorder entry point does not opt in to Stage B1.",
            "Full recorder builder constructs DB and schema; a parallel copy is not read-only.",
            "Cancellation of asyncio.to_thread does not establish DB transaction quiescence.",
            "No operational launcher or independent service restoration is provided.",
        ],
    }


def main() -> int:
    print(json.dumps(review_source_feasibility(), sort_keys=True))
    # Even exact reviewed Git source must never be treated as an operations GO.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
