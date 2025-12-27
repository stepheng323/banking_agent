"""
WhatsApp Flow Response Encryption

Encrypts flow responses using AES-GCM with flipped IV as per Meta's spec.
"""

import base64
import json
from typing import Any

from Crypto.Cipher import AES


def encrypt_flow_response(response: dict[str, Any], aes_key: bytes, iv: bytes) -> str:
    """
    Encrypt a flow response using AES-GCM with flipped IV.

    According to Meta docs:
    - Use same AES key and IV from the decrypted request
    - Flip the IV (XOR with 0xFF) for responses
    - Return base64-encoded encrypted string

    Args:
        response: Response dictionary to encrypt
        aes_key: AES key from decrypted request (bytes)
        iv: Initial vector from decrypted request (bytes)

    Returns:
        Base64-encoded encrypted response string
    """
    try:
        flipped_iv = bytearray()
        for byte in iv:
            flipped_iv.append(byte ^ 0xFF)

        response_json = json.dumps(response).encode("utf-8")

        gcm_cipher = AES.new(aes_key, AES.MODE_GCM, nonce=bytes(flipped_iv))

        encrypted_response, auth_tag = gcm_cipher.encrypt_and_digest(response_json)

        encrypted_with_tag = encrypted_response + auth_tag

        return base64.b64encode(encrypted_with_tag).decode("utf-8")

    except Exception as e:
        print(f"❌ Failed to encrypt flow response: {e}")
        raise
