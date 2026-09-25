from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy" / "bp-phase15-telegram-privileged-handoff.service"


def test_privileged_handoff_unit_is_narrow_and_explicitly_enabled() -> None:
    text = UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=root",
        "Group=root",
        "ConditionPathExists=/etc/bp-telegram-transport/privileged-handoff.env",
        "run_phase15_v3_telegram_privileged_handoff_worker.py",
        "ExecStopPost=/bin/sh -c",
        "telegram-privileged-service-stop",
        "/etc/bp-canary/KILL",
        "ReadOnlyPaths=/opt/bp-telegram-transport /opt/bp-canary /etc/bp-canary/live.env",
        "ReadWritePaths=/etc/bp-canary /var/lib/bp-canary/telegram-live-handoff",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "UnsetEnvironment=POLYMARKET_PRIVATE_KEY",
    ):
        assert marker in text
