from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass


class SecretConfigurationError(RuntimeError):
    """Raised when a required secret is not configured at the SDK boundary."""


@dataclass(frozen=True)
class SecretMetadata:
    private_key_configured: bool
    wallet_configured: bool
    wallet_fingerprint: str | None


def _environment(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def secret_metadata(
    *,
    private_key_env: str,
    wallet_env: str,
    environ: Mapping[str, str] | None = None,
) -> SecretMetadata:
    values = _environment(environ)
    private_key = values.get(private_key_env, "").strip()
    wallet = values.get(wallet_env, "").strip()
    wallet_fingerprint = None
    if wallet:
        wallet_fingerprint = hashlib.sha256(wallet.lower().encode("utf-8")).hexdigest()[:16]
    return SecretMetadata(
        private_key_configured=bool(private_key),
        wallet_configured=bool(wallet),
        wallet_fingerprint=wallet_fingerprint,
    )


def load_private_key_for_sdk(
    *,
    private_key_env: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    private_key = _environment(environ).get(private_key_env, "").strip()
    if not private_key:
        raise SecretConfigurationError("private key is not configured")
    return private_key
