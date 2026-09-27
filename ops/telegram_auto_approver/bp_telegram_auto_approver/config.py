from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_API_ID = "BP_TELEGRAM_API_ID"
ENV_API_HASH = "BP_TELEGRAM_API_HASH"
ENV_SESSION_PATH = "BP_TELEGRAM_SESSION_PATH"
ENV_STATE_PATH = "BP_TELEGRAM_STATE_PATH"
ENV_BOT_USERNAME = "BP_TELEGRAM_EXPECTED_BOT_USERNAME"
ENV_BOT_USER_ID = "BP_TELEGRAM_EXPECTED_BOT_USER_ID"
ENV_OPERATOR_USER_ID = "BP_TELEGRAM_OPERATOR_USER_ID"
ENV_LIVE = "BP_TELEGRAM_AUTO_APPROVE"

_USERNAME = re.compile(r"\A[A-Za-z0-9_]{5,32}\Z")
_API_HASH = re.compile(r"\A[0-9a-fA-F]{32}\Z")


class ConfigError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ServiceConfig:
    api_id: int
    api_hash: str
    session_path: Path
    state_path: Path
    bot_username: str
    bot_user_id: int
    operator_user_id: int
    live: bool


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(
    environ: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> ServiceConfig:
    env = os.environ if environ is None else environ
    repository = repo_root() if root is None else root
    api_id = _positive_int(env.get(ENV_API_ID), "api_id_invalid")
    api_hash = (env.get(ENV_API_HASH) or "").strip()
    if _API_HASH.fullmatch(api_hash) is None:
        raise ConfigError("api_hash_invalid")
    session_path = _outside_file(
        env.get(ENV_SESSION_PATH),
        repository=repository,
        suffix=".session",
        missing_code="session_path_missing",
        invalid_code="session_path_invalid",
    )
    raw_state = env.get(ENV_STATE_PATH)
    if raw_state is None or not raw_state.strip():
        state_path = session_path.with_name(session_path.stem + ".sqlite")
    else:
        state_path = _outside_file(
            raw_state,
            repository=repository,
            suffix=".sqlite",
            missing_code="state_path_missing",
            invalid_code="state_path_invalid",
        )
    if session_path == state_path:
        raise ConfigError("state_path_invalid")
    if not session_path.parent.is_dir() or not state_path.parent.is_dir():
        raise ConfigError("state_directory_missing")
    username = _username(env.get(ENV_BOT_USERNAME))
    bot_user_id = _positive_int(env.get(ENV_BOT_USER_ID), "bot_user_id_invalid")
    operator_user_id = _positive_int(
        env.get(ENV_OPERATOR_USER_ID),
        "operator_user_id_invalid",
    )
    if operator_user_id == bot_user_id:
        raise ConfigError("operator_user_id_invalid")
    live = (env.get(ENV_LIVE) or "").strip() == "true"
    return ServiceConfig(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        state_path=state_path,
        bot_username=username,
        bot_user_id=bot_user_id,
        operator_user_id=operator_user_id,
        live=live,
    )


def _username(value: str | None) -> str:
    raw = (value or "").strip()
    if raw.startswith("@"):
        raw = raw[1:]
    if _USERNAME.fullmatch(raw) is None or not raw.lower().endswith("bot"):
        raise ConfigError("bot_username_invalid")
    return raw.lower()


def _positive_int(value: str | None, code: str) -> int:
    raw = (value or "").strip()
    if not raw.isdigit() or raw.startswith("0"):
        raise ConfigError(code)
    parsed = int(raw)
    if parsed <= 0:
        raise ConfigError(code)
    return parsed


def _outside_file(
    value: str | None,
    *,
    repository: Path,
    suffix: str,
    missing_code: str,
    invalid_code: str,
) -> Path:
    raw = (value or "").strip()
    if not raw:
        raise ConfigError(missing_code)
    path = Path(raw)
    if not path.is_absolute() or path.suffix != suffix or path.is_symlink():
        raise ConfigError(invalid_code)
    resolved = path.resolve()
    repo = repository.resolve()
    if resolved == repo or _is_relative_to(resolved, repo):
        raise ConfigError(invalid_code)
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
