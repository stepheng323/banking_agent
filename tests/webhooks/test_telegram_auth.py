import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest

from apps.gateway.api.webhooks.telegram.auth import validate_telegram_init_data


def _signed_init_data(bot_token: str, payload: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**payload, "hash": calculated_hash})


def test_validate_telegram_init_data_accepts_fresh_payload() -> None:
    bot_token = "123:test-token"
    payload = {
        "auth_date": str(int(time.time())),
        "query_id": "abc",
        "user": '{"id":12345}',
    }

    parsed = validate_telegram_init_data(
        _signed_init_data(bot_token, payload),
        bot_token,
        max_age_seconds=600,
    )

    assert parsed["query_id"] == "abc"


def test_validate_telegram_init_data_rejects_expired_payload() -> None:
    bot_token = "123:test-token"
    payload = {
        "auth_date": str(int(time.time()) - 700),
        "query_id": "abc",
        "user": '{"id":12345}',
    }

    with pytest.raises(ValueError, match="expired"):
        validate_telegram_init_data(
            _signed_init_data(bot_token, payload),
            bot_token,
            max_age_seconds=600,
        )


def test_validate_telegram_init_data_requires_auth_date() -> None:
    bot_token = "123:test-token"
    payload = {
        "query_id": "abc",
        "user": '{"id":12345}',
    }

    with pytest.raises(ValueError, match="auth_date"):
        validate_telegram_init_data(
            _signed_init_data(bot_token, payload),
            bot_token,
            max_age_seconds=600,
        )
