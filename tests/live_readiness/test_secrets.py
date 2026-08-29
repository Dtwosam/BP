import pytest

from bp_engine.live_readiness.secrets import (
    SecretConfigurationError,
    load_private_key_for_sdk,
    secret_metadata,
)


def test_secret_metadata_never_exposes_private_key_value() -> None:
    private_key = "0xthis-is-only-a-test-secret"
    wallet = "0x1111111111111111111111111111111111111111"
    metadata = secret_metadata(
        private_key_env="POLYMARKET_PRIVATE_KEY",
        wallet_env="POLYMARKET_WALLET_ADDRESS",
        environ={
            "POLYMARKET_PRIVATE_KEY": private_key,
            "POLYMARKET_WALLET_ADDRESS": wallet,
        },
    )

    assert metadata.private_key_configured is True
    assert metadata.wallet_configured is True
    assert metadata.wallet_fingerprint is not None
    assert wallet not in repr(metadata)
    assert private_key not in repr(metadata)


def test_secret_metadata_reports_missing_values_without_guessing() -> None:
    metadata = secret_metadata(
        private_key_env="POLYMARKET_PRIVATE_KEY",
        wallet_env="POLYMARKET_WALLET_ADDRESS",
        environ={},
    )
    assert metadata.private_key_configured is False
    assert metadata.wallet_configured is False
    assert metadata.wallet_fingerprint is None


def test_private_key_is_loaded_only_by_explicit_sdk_boundary() -> None:
    private_key = "0xexplicit-test-key"
    assert (
        load_private_key_for_sdk(
            private_key_env="POLYMARKET_PRIVATE_KEY",
            environ={"POLYMARKET_PRIVATE_KEY": private_key},
        )
        == private_key
    )


def test_missing_private_key_raises_generic_non_secret_error() -> None:
    with pytest.raises(SecretConfigurationError, match="private key is not configured") as exc_info:
        load_private_key_for_sdk(private_key_env="POLYMARKET_PRIVATE_KEY", environ={})
    assert "POLYMARKET_PRIVATE_KEY" not in str(exc_info.value)
