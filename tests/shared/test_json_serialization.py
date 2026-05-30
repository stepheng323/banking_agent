"""Tests for strict JSON serialization at infrastructure boundaries."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from shared.queue.redis_stream_publisher import RedisStreamPublisher
from shared.queue.sns_publisher import SNSPublisher
from shared.utils.json import json_dumps_safe, to_json_safe


def test_to_json_safe_serializes_decimal_uuid_and_datetime() -> None:
    payload = {
        "amount_naira": Decimal("2000.50"),
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "created_at": datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
        "nested": [{"amount_naira": Decimal("1.00")}],
    }

    safe = to_json_safe(payload)

    assert safe == {
        "amount_naira": "2000.50",
        "id": "00000000-0000-0000-0000-000000000001",
        "created_at": "2026-05-30T12:00:00+00:00",
        "nested": [{"amount_naira": "1.00"}],
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN")])
def test_json_dumps_safe_rejects_non_finite_numbers(value: object) -> None:
    with pytest.raises(ValueError):
        json_dumps_safe({"amount_naira": value})


async def test_sns_publisher_serializes_decimal_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    published: dict = {}

    class FakeSNSClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def publish(self, **kwargs):
            published.update(kwargs)

    class FakeSession:
        def client(self, *args, **kwargs):
            return FakeSNSClient()

    monkeypatch.setattr("shared.queue.sns_publisher.aioboto3.Session", lambda: FakeSession())

    publisher = SNSPublisher(region_name="us-east-1", topic_arn="arn:test")
    await publisher.publish("payout.process", {"amount_naira": Decimal("2000.00")})

    assert json.loads(published["Message"])["amount_naira"] == "2000.00"


async def test_redis_stream_publisher_serializes_decimal_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    published: dict = {}

    class FakeRedis:
        async def xadd(self, stream_name, fields, **kwargs):
            published["stream_name"] = stream_name
            published["fields"] = fields

    monkeypatch.setattr(
        "shared.queue.redis_stream_publisher.RedisClient.get_client",
        lambda: FakeRedis(),
    )

    publisher = RedisStreamPublisher()
    await publisher.publish("payout.process", {"amount_naira": Decimal("2000.00")})

    assert published["stream_name"] == "async:payouts"
    assert json.loads(published["fields"]["payload"])["amount_naira"] == "2000.00"
