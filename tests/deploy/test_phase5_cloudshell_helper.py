from pathlib import Path


def test_phase5_cloudshell_helper_runs_opt_bp_work_only_remotely() -> None:
    path = Path("scripts/deploy/phase5_cloudshell_accept.sh")
    source = path.read_text(encoding="utf-8")

    assert "gcloud compute ssh" in source
    assert "REMOTE_SCRIPT=" in source
    remote_index = source.index("REMOTE_SCRIPT=")
    assert "/opt/bp" not in source[:remote_index]
    assert "git -C /opt/bp fetch" in source[remote_index:]
    assert "phase5_host_acceptance.sh" in source[remote_index:]
    assert "PHASE5_HEAD" in source
