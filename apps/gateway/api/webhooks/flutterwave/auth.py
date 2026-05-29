"""Flutterwave webhook authentication helpers."""

import base64
import hashlib
import hmac

from fastapi import Request

from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)

FLUTTERWAVE_SIGNATURE_HEADER = "flutterwave-signature"
FLUTTERWAVE_VERIF_HASH_HEADER = "verif-hash"


def is_authorized_flutterwave_webhook(request: Request, raw_body: bytes) -> bool:
    """Validate Flutterwave webhook signature before parsing provider payloads."""
    configured_secret = settings.flutterwave_webhook_secret_hash
    if not configured_secret:
        logger.error("flutterwave_webhook_secret_not_configured")
        return False

    signature = request.headers.get(FLUTTERWAVE_SIGNATURE_HEADER, "")
    if signature:
        expected = base64.b64encode(
            hmac.new(configured_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        ).decode("utf-8")
        if hmac.compare_digest(signature, expected):
            return True
        logger.warning("flutterwave_webhook_invalid_signature", signature_hash=log_fingerprint(signature))
        return False

    verif_hash = request.headers.get(FLUTTERWAVE_VERIF_HASH_HEADER, "")
    if verif_hash and hmac.compare_digest(verif_hash, configured_secret):
        return True

    logger.warning("flutterwave_webhook_missing_signature")
    return False
