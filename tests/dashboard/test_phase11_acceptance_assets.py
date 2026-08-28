from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_ACCEPTANCE = ROOT / "scripts" / "deploy" / "phase11_host_acceptance.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_phase11_host_acceptance_does_not_leave_function_return_traps() -> None:
    script = HOST_ACCEPTANCE.read_text(encoding="utf-8")

    assert "trap 'rm -rf \"$tmp\"' RETURN" not in script
    assert "trap cleanup_node_tmp RETURN" not in script
    assert 'rm -rf "$tmp"' in script


def test_phase11_host_acceptance_sets_node_path_for_npm_version_probe() -> None:
    script = HOST_ACCEPTANCE.read_text(encoding="utf-8")

    probe = re.compile(
        r'env PATH="\$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"'
        r'\s*\\\s*"\$NODE_ROOT/bin/npm" --version'
    )
    assert probe.search(script)


def test_ci_syntax_checks_phase11_production_installer() -> None:
    workflow = CI.read_text(encoding="utf-8")

    assert "bash -n scripts/deploy/phase11_install.sh" in workflow
