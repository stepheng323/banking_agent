"""Tests for Flutterwave webhook ingress."""

import base64
import hashlib
import hmac
import importlib
import json
from unittest.mock import AsyncMock

import pytest

from apps.gateway.api.webhooks.flutterwave.service import FlutterwaveWebhookService


class _FakeFlutterwaveRequest:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None, raw_body: bytes | None = None) -> None:
        self._raw_body = raw_body if raw_body is not None else json.dumps(payload, separators=(",", ":")).encode()
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._raw_body


class _RouterServiceStub:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.payloads: list[dict] = []

    async def handle_event(self, payload: dict) -> bool:
        self.payloads.append(payload)
        return self.result


class _FailingRouterServiceStub(_RouterServiceStub):
    async def handle_event(self, payload: dict) -> bool:
        self.payloads.append(payload)
        raise RuntimeError("publish failed")


class _WebhookEventLedger:
    def __init__(self, *, claim_result: bool = True) -> None:
        self.claim_result = claim_result
        self.claim_calls: list[dict] = []
        self.processed: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, str]] = []

    async def claim(
        self,
        *,
        provider: str,
        event_id: str,
        event_name: str,
        payload_hash: str | None = None,
    ) -> bool:
        self.claim_calls.append(
            {
                "provider": provider,
                "event_id": event_id,
                "event_name": event_name,
                "payload_hash": payload_hash,
            }
        )
        return self.claim_result

    async def mark_processed(self, *, provider: str, event_id: str) -> None:
        self.processed.append((provider, event_id))

    async def mark_failed(self, *, provider: str, event_id: str, error_message: str | None = None) -> None:
        self.failed.append((provider, event_id, error_message or ""))


class _WebhookUow:
    def __init__(self, ledger: _WebhookEventLedger | None) -> None:
        self.processed_webhook_events = ledger

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


def _signature(secret: str, raw_body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()).decode()


def _payload() -> dict:
    return {
        "event": "transfer.completed",
        "event.type": "Transfer",
        "webhook_id": "evt-flw-1",
        "data": {
            "id": 12345,
            "reference": "idem-1",
            "status": "SUCCESSFUL",
            "amount": 5000,
            "currency": "NGN",
        },
    }


def _current_transfer_payload() -> dict:
    return {
        "type": "transfer.disburse",
        "webhook_id": "evt-flw-2",
        "data": {
            "id": "trf_12345",
            "type": "BANK",
            "reference": "idem-2",
            "status": "SUCCESSFUL",
            "amount": 7000,
            "destination_currency": "NGN",
        },
    }


@pytest.mark.asyncio
async def test_flutterwave_webhook_rejects_missing_signature_outside_local(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    service = _RouterServiceStub()
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", "expected-secret")
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)

    response = await flutterwave_router.flutterwave_webhook(_FakeFlutterwaveRequest(_payload()))

    assert response.status_code == 401
    assert service.payloads == []


@pytest.mark.asyncio
async def test_flutterwave_webhook_accepts_hmac_signature_claims_and_routes(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_event_ledger = importlib.import_module("apps.gateway.api.webhooks.flutterwave.event_ledger")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    payload = _payload()
    raw_body = json.dumps(payload, separators=(",", ":")).encode()
    secret = "expected-secret"
    service = _RouterServiceStub()
    ledger = _WebhookEventLedger()
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", secret)
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)
    monkeypatch.setattr(flutterwave_event_ledger, "UnitOfWork", lambda: _WebhookUow(ledger))

    response = await flutterwave_router.flutterwave_webhook(
        _FakeFlutterwaveRequest(
            payload,
            headers={"flutterwave-signature": _signature(secret, raw_body)},
            raw_body=raw_body,
        )
    )

    assert response.status_code == 200
    assert service.payloads == [payload]
    assert ledger.claim_calls == [
        {
            "provider": "flutterwave",
            "event_id": "evt-flw-1",
            "event_name": "transfer.completed",
            "payload_hash": flutterwave_event_ledger.payload_hash(payload),
        }
    ]
    assert ledger.processed == [("flutterwave", "evt-flw-1")]
    assert ledger.failed == []


@pytest.mark.asyncio
async def test_flutterwave_webhook_accepts_v3_verif_hash(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_event_ledger = importlib.import_module("apps.gateway.api.webhooks.flutterwave.event_ledger")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    payload = _payload()
    service = _RouterServiceStub()
    ledger = _WebhookEventLedger()
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", "expected-secret")
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)
    monkeypatch.setattr(flutterwave_event_ledger, "UnitOfWork", lambda: _WebhookUow(ledger))

    response = await flutterwave_router.flutterwave_webhook(
        _FakeFlutterwaveRequest(payload, headers={"verif-hash": "expected-secret"})
    )

    assert response.status_code == 200
    assert service.payloads == [payload]
    assert ledger.processed == [("flutterwave", "evt-flw-1")]


@pytest.mark.asyncio
async def test_flutterwave_webhook_duplicate_event_skips_business_routing(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_event_ledger = importlib.import_module("apps.gateway.api.webhooks.flutterwave.event_ledger")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    payload = _payload()
    service = _RouterServiceStub()
    ledger = _WebhookEventLedger(claim_result=False)
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", "expected-secret")
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)
    monkeypatch.setattr(flutterwave_event_ledger, "UnitOfWork", lambda: _WebhookUow(ledger))

    response = await flutterwave_router.flutterwave_webhook(
        _FakeFlutterwaveRequest(payload, headers={"verif-hash": "expected-secret"})
    )

    assert response.status_code == 200
    assert service.payloads == []
    assert ledger.claim_calls[0]["event_id"] == "evt-flw-1"
    assert ledger.processed == []


@pytest.mark.asyncio
async def test_flutterwave_webhook_marks_event_failed_and_returns_retryable_error(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_event_ledger = importlib.import_module("apps.gateway.api.webhooks.flutterwave.event_ledger")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    payload = _payload()
    service = _FailingRouterServiceStub()
    ledger = _WebhookEventLedger()
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", "expected-secret")
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)
    monkeypatch.setattr(flutterwave_event_ledger, "UnitOfWork", lambda: _WebhookUow(ledger))

    response = await flutterwave_router.flutterwave_webhook(
        _FakeFlutterwaveRequest(payload, headers={"verif-hash": "expected-secret"})
    )

    assert response.status_code == 500
    assert ledger.processed == []
    assert ledger.failed == [("flutterwave", "evt-flw-1", "publish failed")]


@pytest.mark.asyncio
async def test_flutterwave_webhook_event_ledger_missing_fails_retryably(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_event_ledger = importlib.import_module("apps.gateway.api.webhooks.flutterwave.event_ledger")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    payload = _payload()
    service = _RouterServiceStub()
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", "expected-secret")
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)
    monkeypatch.setattr(flutterwave_event_ledger, "UnitOfWork", lambda: _WebhookUow(None))

    response = await flutterwave_router.flutterwave_webhook(
        _FakeFlutterwaveRequest(payload, headers={"verif-hash": "expected-secret"})
    )

    assert response.status_code == 500
    assert service.payloads == []


@pytest.mark.asyncio
async def test_flutterwave_webhook_does_not_mark_failed_after_successful_action(monkeypatch) -> None:
    flutterwave_auth = importlib.import_module("apps.gateway.api.webhooks.flutterwave.auth")
    flutterwave_dependencies = importlib.import_module("apps.gateway.api.webhooks.flutterwave.dependencies")
    flutterwave_event_ledger = importlib.import_module("apps.gateway.api.webhooks.flutterwave.event_ledger")
    flutterwave_router = importlib.import_module("apps.gateway.api.webhooks.flutterwave.router")

    payload = _payload()
    service = _RouterServiceStub()
    failed_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(flutterwave_auth.settings.runtime, "app_env", "production")
    monkeypatch.setattr(flutterwave_auth.settings, "flutterwave_webhook_secret_hash", "expected-secret")
    monkeypatch.setattr(flutterwave_dependencies, "get_flutterwave_webhook_service", lambda: service)
    monkeypatch.setattr(flutterwave_event_ledger, "claim_flutterwave_webhook_event", AsyncMock(return_value=True))

    async def _raise_mark_processed(*, event_id: str) -> None:
        del event_id
        raise RuntimeError("ledger write failed after publish")

    async def _capture_mark_failed(*, event_id: str, error: str) -> None:
        failed_calls.append((event_id, error))

    monkeypatch.setattr(flutterwave_event_ledger, "mark_flutterwave_webhook_event_processed", _raise_mark_processed)
    monkeypatch.setattr(flutterwave_event_ledger, "mark_flutterwave_webhook_event_failed", _capture_mark_failed)

    response = await flutterwave_router.flutterwave_webhook(
        _FakeFlutterwaveRequest(payload, headers={"verif-hash": "expected-secret"})
    )

    assert response.status_code == 500
    assert service.payloads == [payload]
    assert failed_calls == []


@pytest.mark.asyncio
async def test_flutterwave_webhook_service_queues_reconciliation_job() -> None:
    publisher = _CapturePublisher()
    service = FlutterwaveWebhookService(publisher=publisher)

    handled = await service.handle_event(_payload())

    assert handled is True
    assert publisher.published == [
        (
            "payout.reconcile",
            {
                "source": "flutterwave_webhook",
                "event_id": "evt-flw-1",
                "event_name": "transfer.completed",
                "reference": "idem-1",
                "provider_transfer_id": "12345",
                "status_hint": "SUCCESSFUL",
            },
        )
    ]


@pytest.mark.asyncio
async def test_flutterwave_webhook_service_accepts_current_transfer_disburse_event() -> None:
    publisher = _CapturePublisher()
    service = FlutterwaveWebhookService(publisher=publisher)

    handled = await service.handle_event(_current_transfer_payload())

    assert handled is True
    assert publisher.published == [
        (
            "payout.reconcile",
            {
                "source": "flutterwave_webhook",
                "event_id": "evt-flw-2",
                "event_name": "transfer.disburse",
                "reference": "idem-2",
                "provider_transfer_id": "trf_12345",
                "status_hint": "SUCCESSFUL",
            },
        )
    ]


@pytest.mark.asyncio
async def test_flutterwave_webhook_service_ignores_non_transfer_events() -> None:
    publisher = _CapturePublisher()
    service = FlutterwaveWebhookService(publisher=publisher)

    handled = await service.handle_event(
        {
            "type": "charge.completed",
            "webhook_id": "evt-charge-1",
            "data": {
                "id": "chg_123",
                "type": "BANK",
                "reference": "idem-1",
                "status": "succeeded",
            },
        }
    )

    assert handled is True
    assert publisher.published == []


@pytest.mark.asyncio
async def test_flutterwave_webhook_service_ignores_bank_shaped_payload_without_transfer_event() -> None:
    publisher = _CapturePublisher()
    service = FlutterwaveWebhookService(publisher=publisher)

    handled = await service.handle_event(
        {
            "type": "refund.completed",
            "webhook_id": "evt-refund-1",
            "data": {
                "id": "ref_123",
                "type": "BANK",
                "reference": "idem-1",
                "status": "succeeded",
            },
        }
    )

    assert handled is True
    assert publisher.published == []
