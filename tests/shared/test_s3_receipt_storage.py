from datetime import datetime
from typing import Any

import pytest

from shared.clients.storage.s3_client import S3Client
from shared.utils.logging import log_fingerprint


class _FakeS3:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []
        self.list_response: dict[str, Any] = {}

    async def __aenter__(self) -> "_FakeS3":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    async def put_object(self, **kwargs: Any) -> None:
        self.put_calls.append(kwargs)

    async def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.presign_calls.append({"list": kwargs})
        return self.list_response

    def generate_presigned_url(self, method: str, **kwargs: Any) -> str:
        self.presign_calls.append({"method": method, **kwargs})
        return "https://signed.example/receipt"


class _FakeSession:
    def __init__(self, s3: _FakeS3) -> None:
        self.s3 = s3

    def client(self, *_args: Any, **_kwargs: Any) -> _FakeS3:
        return self.s3


@pytest.mark.asyncio
async def test_receipt_upload_is_private_and_returns_presigned_url(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_s3 = _FakeS3()
    monkeypatch.setattr("shared.clients.storage.s3_client.secrets.token_urlsafe", lambda _: "random-nonce")
    monkeypatch.setattr("shared.clients.storage.s3_client.aioboto3.Session", lambda: _FakeSession(fake_s3))

    client = S3Client()
    client.bucket_name = "receipt-bucket"
    client.receipt_prefix = "receipts"

    url = await client.upload_receipt_image(
        b"png-bytes",
        transaction_id="tx-secret-123",
        timestamp=datetime(2026, 5, 19, 12, 0, 0),
    )

    assert url == "https://signed.example/receipt"
    assert len(fake_s3.put_calls) == 1
    put_call = fake_s3.put_calls[0]
    assert "ACL" not in put_call
    assert put_call["Key"] == f"receipts/{log_fingerprint('tx-secret-123', length=24)}/random-nonce.png"
    assert "tx-secret-123" not in put_call["Key"]
    assert "20260519" not in put_call["Key"]
    assert fake_s3.presign_calls[-1] == {
        "method": "get_object",
        "Params": {"Bucket": "receipt-bucket", "Key": put_call["Key"]},
        "ExpiresIn": 900,
    }
