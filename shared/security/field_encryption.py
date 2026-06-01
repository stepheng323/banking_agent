"""Field-level encryption and blind indexes for persisted bank identifiers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from Crypto.Cipher import AES

from shared.config.settings import settings
from shared.utils.sanitize import sanitize_account_number

_PREFIX = "enc"
_VERSION = "v1"
_DEV_ENCRYPTION_KEY = hashlib.sha256(b"banking-agent-local-field-encryption").digest()
_DEV_BLIND_INDEX_KEY = hashlib.sha256(b"banking-agent-local-blind-index").digest()


class FieldEncryptionError(ValueError):
    """Raised when encrypted field data or key material is invalid."""


@dataclass(frozen=True, slots=True)
class EncryptedFieldValue:
    """Encrypted field payload and safe lookup/display helpers."""

    ciphertext: str
    blind_index: str | None
    last4: str | None


def is_encrypted_value(value: object) -> bool:
    """Return whether a string already uses the field-encryption envelope."""
    return isinstance(value, str) and value.startswith(f"{_PREFIX}:{_VERSION}:")


def normalize_account_number(value: object) -> str:
    """Normalize bank account numbers before encrypting or indexing."""
    raw = decrypt_optional(value) if is_encrypted_value(value) else value
    return sanitize_account_number(str(raw or ""))


def normalize_secret_identifier(value: object) -> str:
    """Normalize provider identifiers such as Mono mandate IDs."""
    raw = decrypt_optional(value) if is_encrypted_value(value) else value
    return str(raw or "").strip()


def account_number_last4(value: object) -> str | None:
    """Return the last four digits of a bank account number."""
    normalized = normalize_account_number(value)
    return normalized[-4:] if normalized else None


def encrypt_account_number(value: object, *, field: str) -> EncryptedFieldValue:
    """Encrypt a bank account number and return lookup/display helpers."""
    normalized = normalize_account_number(value)
    if not normalized:
        return EncryptedFieldValue(ciphertext="", blind_index=None, last4=None)
    return EncryptedFieldValue(
        ciphertext=encrypt_text(normalized),
        blind_index=blind_index(field, normalized),
        last4=normalized[-4:],
    )


def encrypt_secret_identifier(value: object, *, field: str) -> EncryptedFieldValue:
    """Encrypt a non-display provider identifier such as a mandate ID."""
    normalized = normalize_secret_identifier(value)
    if not normalized:
        return EncryptedFieldValue(ciphertext="", blind_index=None, last4=None)
    return EncryptedFieldValue(
        ciphertext=encrypt_text(normalized),
        blind_index=blind_index(field, normalized),
        last4=None,
    )


def decrypt_optional(value: object) -> str | None:
    """Decrypt encrypted text, returning plaintext strings unchanged for migration tolerance."""
    if value is None:
        return None
    raw = str(value)
    if raw == "":
        return ""
    if not is_encrypted_value(raw):
        return raw
    return decrypt_text(raw)


def encrypt_text(value: str) -> str:
    """Encrypt text using AES-GCM with a random nonce."""
    plaintext = str(value or "")
    if plaintext == "":
        return ""
    if is_encrypted_value(plaintext):
        return plaintext
    key = _encryption_key()
    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    return ":".join(
        [
            _PREFIX,
            _VERSION,
            _key_id(),
            _b64encode(nonce),
            _b64encode(ciphertext),
            _b64encode(tag),
        ]
    )


def decrypt_text(value: str) -> str:
    """Decrypt an AES-GCM field-encryption envelope."""
    parts = str(value or "").split(":")
    if len(parts) != 6 or parts[0] != _PREFIX or parts[1] != _VERSION:
        raise FieldEncryptionError("invalid encrypted field envelope")
    _prefix, _version, _key_id_value, nonce_b64, ciphertext_b64, tag_b64 = parts
    try:
        nonce = _b64decode(nonce_b64)
        ciphertext = _b64decode(ciphertext_b64)
        tag = _b64decode(tag_b64)
        cipher = AES.new(_encryption_key(), AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except Exception as exc:
        raise FieldEncryptionError("encrypted field decrypt failed") from exc


def blind_index(field: str, value: object, *, normalizer: str = "text") -> str | None:
    """Return a deterministic keyed hash for equality lookup."""
    if normalizer == "account_number":
        normalized = normalize_account_number(value)
    else:
        normalized = normalize_secret_identifier(value)
    if not normalized:
        return None
    payload = f"{field}:{normalized}".encode()
    return hmac.new(_blind_index_key(), payload, hashlib.sha256).hexdigest()


def require_field_encryption_ready() -> None:
    """Validate key configuration for startup or migrations."""
    _encryption_key()
    _blind_index_key()


def _key_id() -> str:
    key_id = settings.field_encryption_key_id.strip()
    return key_id or "local-dev"


def _encryption_key() -> bytes:
    return _configured_key(
        settings.field_encryption_key_b64,
        fallback=_DEV_ENCRYPTION_KEY,
        name="FIELD_ENCRYPTION_KEY_B64",
    )


def _blind_index_key() -> bytes:
    return _configured_key(
        settings.field_blind_index_key_b64,
        fallback=_DEV_BLIND_INDEX_KEY,
        name="FIELD_BLIND_INDEX_KEY_B64",
    )


def _configured_key(raw_b64: str, *, fallback: bytes, name: str) -> bytes:
    raw = (raw_b64 or "").strip()
    if not raw:
        if settings.runtime.is_local:
            return fallback
        raise FieldEncryptionError(f"{name} is required outside local/test environments")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise FieldEncryptionError(f"{name} must be valid base64") from exc
    if len(key) not in {16, 24, 32}:
        raise FieldEncryptionError(f"{name} must decode to 16, 24, or 32 bytes")
    return key


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
