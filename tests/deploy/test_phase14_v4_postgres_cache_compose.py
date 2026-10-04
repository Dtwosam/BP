from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.prod.yml"


def test_postgres_shared_buffers_is_env_driven_with_safe_default() -> None:
    source = COMPOSE.read_text(encoding="utf-8")
    assert "shared_buffers=${POSTGRES_SHARED_BUFFERS:-128MB}" in source
    assert source.count("shared_buffers=") == 1
    assert "POSTGRES_SHARED_BUFFERS=2GB" not in source
