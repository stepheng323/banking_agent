"""Tests for field-level bank identifier encryption helpers."""

import base64

import pytest

from shared.config.settings import settings
from shared.security.field_encryption import (
    FieldEncryptionError,
    blind_index,
    decrypt_optional,
    encrypt_account_number,
    encrypt_secret_identifier,
    is_encrypted_value,
    require_field_encryption_ready,
)
from shared.security.redaction import mask_account_number, redact_sensitive_identifiers


def test_account_number_encrypt_decrypt_round_trip() -> None:
    encrypted = encrypt_account_number("1234-567-890", field="accounts.account_number")

    assert is_encrypted_value(encrypted.ciphertext)
    assert decrypt_optional(encrypted.ciphertext) == "1234567890"
    assert encrypted.last4 == "7890"


def test_secret_identifier_encrypt_decrypt_round_trip() -> None:
    encrypted = encrypt_secret_identifier(" mandate_abc ", field="accounts.mandate_id")

    assert is_encrypted_value(encrypted.ciphertext)
    assert decrypt_optional(encrypted.ciphertext) == "mandate_abc"
    assert encrypted.last4 is None


def test_tampered_ciphertext_fails_decrypt() -> None:
    encrypted = encrypt_account_number("1234567890", field="accounts.account_number")
    parts = encrypted.ciphertext.split(":")
    parts[4] = f"{'A' if parts[4][0] != 'A' else 'B'}{parts[4][1:]}"
    tampered = ":".join(parts)

    with pytest.raises(FieldEncryptionError):
        decrypt_optional(tampered)


def test_blind_index_is_deterministic_and_field_scoped() -> None:
    first = blind_index("accounts.account_number", "1234567890", normalizer="account_number")
    second = blind_index("accounts.account_number", "123-456-7890", normalizer="account_number")
    other_field = blind_index("transactions.source_account_number", "1234567890", normalizer="account_number")

    assert first == second
    assert first != other_field


def test_missing_keys_fail_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.runtime, "app_env", "production")
    monkeypatch.setattr(settings, "field_encryption_key_b64", "")
    monkeypatch.setattr(settings, "field_blind_index_key_b64", "")

    with pytest.raises(FieldEncryptionError):
        require_field_encryption_ready()


def test_configured_keys_are_accepted_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.b64encode(b"1" * 32).decode("ascii")
    blind_key = base64.b64encode(b"2" * 32).decode("ascii")
    monkeypatch.setattr(settings.runtime, "app_env", "production")
    monkeypatch.setattr(settings, "field_encryption_key_b64", key)
    monkeypatch.setattr(settings, "field_blind_index_key_b64", blind_key)

    require_field_encryption_ready()


def test_redaction_masks_account_numbers_and_fingerprints_mandates() -> None:
    payload = {
        "account_number": "1234567890",
        "mandate_id": "mandate_abc",
        "account": {"account_number": "0123456789"},
    }

    redacted = redact_sensitive_identifiers(payload)

    assert redacted["account_number"] == mask_account_number("1234567890")
    assert redacted["mandate_id"] != "mandate_abc"
    assert redacted["account"]["account_number"] == mask_account_number("0123456789")
