"""
WhatsApp Flow Decryption Utilities

Handles decryption of WhatsApp Flow encrypted data using RSA-OAEP and AES-GCM.

According to Meta docs:
 https://developers.facebook.com/docs/whatsapp/flows/guides/implementingyourflowendpoint
- RSA-OAEP with SHA256 for AES key encryption
- AES-GCM (Galois/Counter Mode) for data encryption
- 128-bit nonce (IV) for GCM mode
"""

import base64
import json
import os
from typing import Any

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA


def get_private_key_from_env() -> str:
    """
    Get the RSA private key from file path.

    Reads the private key from the file specified by whatsapp_flow_private_key_path env variable.

    Returns:
        Private key as string, or None if not found
    """
    key_path = os.getenv("WHATSAPP_FLOW_PRIVATE_KEY_PATH") or os.getenv(
        "whatsapp_flow_private_key_path"
    )

    if not key_path:
        raise FileNotFoundError(
            "Environment variable WHATSAPP_FLOW_PRIVATE_KEY_PATH not set or empty"
        )

    if not os.path.exists(key_path):
        raise FileNotFoundError(f"Private key file not found at: {key_path}")

    with open(key_path, encoding="utf-8") as f:
        return f.read()


def decrypt_flow_data(
    encrypted_data: str, encrypted_key: str, iv: str, private_key_pem: str = ""
) -> tuple[dict[str, Any], bytes, bytes] | None:
    """
    Decrypt WhatsApp Flow encrypted data.

    WhatsApp encrypts flow data using:
    1. RSA-OAEP with SHA256 for the AES key
    2. AES-GCM with 128-bit nonce for the data

    Args:
        encrypted_data: Base64 encoded encrypted flow data
        encrypted_key: RSA-encrypted AES key (Base64)
        iv: Initial vector (Base64) - 128-bit nonce for GCM
        private_key_pem: RSA private key in PEM format (optional, from env)

    Returns:
        Decrypted flow data as dictionary
    """
    try:
        if not private_key_pem:
            private_key_pem = get_private_key_from_env()

        if not private_key_pem:
            raise FileNotFoundError(
                "No private key configured for flow decryption. "
                "Set WHATSAPP_FLOW_PRIVATE_KEY_PATH environment variable."
            )

        private_key = RSA.import_key(private_key_pem)
        encrypted_key_bytes = base64.b64decode(encrypted_key)

        cipher_rsa = PKCS1_OAEP.new(private_key, hashAlgo=SHA256)
        aes_key = cipher_rsa.decrypt(encrypted_key_bytes)

        encrypted_data_bytes = base64.b64decode(encrypted_data)
        iv_bytes = base64.b64decode(iv)

        if len(encrypted_data_bytes) < 16:
            raise ValueError("GCM data too short for auth tag")

        ciphertext = encrypted_data_bytes[:-16]
        auth_tag = encrypted_data_bytes[-16:]

        gcm_cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv_bytes)  # type: ignore[call-overload]
        plaintext = gcm_cipher.decrypt_and_verify(ciphertext, auth_tag)

        flow_data = json.loads(plaintext.decode("utf-8"))
        return flow_data, aes_key, iv_bytes

    except ValueError as ve:
        if "Incorrect decryption" in str(ve):
            print("   ❌ RSA decryption failed")
        else:
            print(f"   ❌ Decryption error: {ve}")
        import traceback

        traceback.print_exc()
        return None
    except Exception as e:
        print(f"   ❌ Failed to decrypt flow data: {e}")
        import traceback

        traceback.print_exc()
        return None


def is_encrypted(body: dict[str, Any]) -> bool:
    """
    Check if the incoming request body contains encrypted flow data.

    Args:
        body: Request body dictionary

    Returns:
        True if encrypted, False otherwise
    """
    return "encrypted_flow_data" in body
