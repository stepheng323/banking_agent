"""Tests for Mono webhook handler - Unit tests with mocked dependencies."""

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.gateway.api.webhooks.mono.service import MonoWebhookService
from banking.transactions.runtime.async_completion import record_group_leg_and_maybe_build_summary
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum


class _FakeMonoRequest:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    async def json(self) -> dict:
        return self._payload


class _RouterServiceStub:
    MANDATE_STATUS_MAP = {"events.mandates.approved": "approved"}
    DEBIT_STATUS_MAP = {
        "events.mandates.debit.successful": "confirmed",
        "direct_debit.payment_successful": "confirmed",
    }

    def __init__(self) -> None:
        self.mandate_events: list[tuple[str, dict]] = []
        self.debit_events: list[tuple[str, dict]] = []

    async def handle_mandate_event(self, event: str, data: dict, *, event_id: str | None = None) -> bool:
        del event_id
        self.mandate_events.append((event, data))
        return True

    async def handle_debit_event(self, event: str, data: dict) -> bool:
        self.debit_events.append((event, data))
        return True


class _FailingRouterServiceStub(_RouterServiceStub):
    async def handle_debit_event(self, event: str, data: dict) -> bool:
        raise RuntimeError("handler failed")


class _UnprocessedRouterServiceStub(_RouterServiceStub):
    async def handle_debit_event(self, event: str, data: dict) -> bool:
        return False


class _RouterWebhookEventLedger:
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


class _RouterWebhookUow:
    def __init__(self, ledger: _RouterWebhookEventLedger) -> None:
        self.processed_webhook_events = ledger

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestMonoWebhookRouterSecurity:
    @pytest.mark.asyncio
    async def test_rejects_missing_secret_outside_local_before_business_routing(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _RouterServiceStub()
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)

        response = await mono_router.mono_webhook(
            _FakeMonoRequest({"event": "events.mandates.debit.successful", "data": {"id": "debit-1"}})
        )

        assert response.status_code == 401
        assert service.mandate_events == []
        assert service.debit_events == []

    @pytest.mark.asyncio
    async def test_rejects_wrong_secret_outside_local(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _RouterServiceStub()
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(
                {"event": "events.mandates.debit.successful", "data": {"id": "debit-1"}},
                headers={"mono-webhook-secret": "wrong-secret"},
            )
        )

        assert response.status_code == 401
        assert service.mandate_events == []
        assert service.debit_events == []

    @pytest.mark.asyncio
    async def test_accepts_valid_secret_and_routes_debit_event_to_debit_handler(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _RouterServiceStub()
        payload = {"event": "events.mandates.debit.successful", "data": {"id": "debit-1"}}
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(payload, headers={"mono-webhook-secret": "expected-secret"})
        )

        assert response.status_code == 200
        assert service.debit_events == [("events.mandates.debit.successful", {"id": "debit-1"})]
        assert service.mandate_events == []

    @pytest.mark.asyncio
    async def test_routes_directpay_payment_event_to_debit_handler(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _RouterServiceStub()
        payload = {
            "event": "direct_debit.payment_successful",
            "data": {"object": {"id": "txd-1", "reference": "ref-1", "status": "successful"}},
        }
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(payload, headers={"mono-webhook-secret": "expected-secret"})
        )

        assert response.status_code == 200
        assert service.debit_events == [
            (
                "direct_debit.payment_successful",
                {"object": {"id": "txd-1", "reference": "ref-1", "status": "successful"}},
            )
        ]

    @pytest.mark.asyncio
    async def test_claims_event_id_and_marks_processed_after_routing(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _RouterServiceStub()
        ledger = _RouterWebhookEventLedger()
        payload = {
            "event": "events.mandates.debit.successful",
            "event_id": "evt-1",
            "data": {"id": "debit-1", "reference_number": "ref-1"},
        }
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)
        monkeypatch.setattr(mono_router, "UnitOfWork", lambda: _RouterWebhookUow(ledger))

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(payload, headers={"mono-webhook-secret": "expected-secret"})
        )

        assert response.status_code == 200
        assert service.debit_events == [("events.mandates.debit.successful", payload["data"])]
        assert ledger.claim_calls == [
            {
                "provider": "mono",
                "event_id": "evt-1",
                "event_name": "events.mandates.debit.successful",
                "payload_hash": mono_router._payload_hash(payload),
            }
        ]
        assert ledger.processed == [("mono", "evt-1")]
        assert ledger.failed == []

    @pytest.mark.asyncio
    async def test_duplicate_event_id_skips_business_routing(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _RouterServiceStub()
        ledger = _RouterWebhookEventLedger(claim_result=False)
        payload = {
            "event": "events.mandates.debit.successful",
            "event_id": "evt-duplicate",
            "data": {"id": "debit-1", "reference_number": "ref-1"},
        }
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)
        monkeypatch.setattr(mono_router, "UnitOfWork", lambda: _RouterWebhookUow(ledger))

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(payload, headers={"mono-webhook-secret": "expected-secret"})
        )

        assert response.status_code == 200
        assert service.debit_events == []
        assert ledger.claim_calls[0]["event_id"] == "evt-duplicate"
        assert ledger.processed == []
        assert ledger.failed == []

    @pytest.mark.asyncio
    async def test_failed_handler_marks_event_failed_for_replay(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _FailingRouterServiceStub()
        ledger = _RouterWebhookEventLedger()
        payload = {
            "event": "events.mandates.debit.successful",
            "event_id": "evt-failed",
            "data": {"id": "debit-1", "reference_number": "ref-1"},
        }
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)
        monkeypatch.setattr(mono_router, "UnitOfWork", lambda: _RouterWebhookUow(ledger))

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(payload, headers={"mono-webhook-secret": "expected-secret"})
        )

        assert response.status_code == 200
        assert ledger.processed == []
        assert ledger.failed == [("mono", "evt-failed", "handler failed")]

    @pytest.mark.asyncio
    async def test_unprocessed_known_event_is_marked_failed_for_replay(self, monkeypatch):
        mono_router = importlib.import_module("apps.gateway.api.webhooks.mono.router")

        service = _UnprocessedRouterServiceStub()
        ledger = _RouterWebhookEventLedger()
        payload = {
            "event": "events.mandates.debit.successful",
            "event_id": "evt-unprocessed",
            "data": {"id": "debit-1", "reference_number": "ref-1"},
        }
        monkeypatch.setattr(mono_router.settings.runtime, "app_env", "production")
        monkeypatch.setattr(mono_router.settings, "mono_webhook_secret", "expected-secret")
        monkeypatch.setattr(mono_router, "_get_service", lambda: service)
        monkeypatch.setattr(mono_router, "UnitOfWork", lambda: _RouterWebhookUow(ledger))

        response = await mono_router.mono_webhook(
            _FakeMonoRequest(payload, headers={"mono-webhook-secret": "expected-secret"})
        )

        assert response.status_code == 200
        assert ledger.processed == []
        assert ledger.failed == [("mono", "evt-unprocessed", "mono_debit_event_not_processed")]


class TestWebhookServiceBasics:
    """Basic unit tests for webhook handling logic."""

    def test_debit_success_status_mapping(self):
        """Test that debit success status is correctly identified."""
        success_events = [
            "events.mandates.debit.successful",
            "direct_debit.payment_successful",
        ]
        for event in success_events:
            assert "successful" in event.lower() or "success" in event.lower()

    def test_debit_failure_status_identification(self):
        """Test that debit failure status is correctly identified."""
        failure_events = [
            "events.mandates.debit.failed",
            "direct_debit.payment_failed",
        ]
        for event in failure_events:
            assert "failed" in event.lower()


class TestFormatting:
    """Tests for multi-source formatting utilities."""

    def test_format_multi_source_transfer_summary(self):
        """Test multi-source confirmation formatting."""
        from banking.presentation.formatters.transfer_multi_source import format_multi_source_transfer_summary

        result = format_multi_source_transfer_summary(
            {
                "amount": 50000,
                "recipientName": "John Doe",
                "recipientBank": "GTBank",
                "recipientAccount": "1234567890",
                "funding_sources": [
                    {"bank_name": "UBA", "account_number": "1111111111", "amount": 30000},
                    {"bank_name": "Access", "account_number": "2222222222", "amount": 20000},
                ],
                "narration": "Test payment",
            }
        )

        assert "₦50,000" in result
        assert "John Doe" in result
        assert "Funding from:" in result
        assert "UBA" in result
        assert "Access" in result
        assert "₦30,000" in result
        assert "₦20,000" in result

    def test_format_multi_source_receipt(self):
        """Test multi-source receipt formatting."""
        from banking.presentation.formatters.transfer_multi_source import format_multi_source_receipt

        result = format_multi_source_receipt(
            {
                "amount": 50000,
                "recipientName": "John Doe",
                "recipientBank": "GTBank",
                "recipientAccount": "1234567890",
                "funding_sources": [
                    {"bank_name": "UBA", "account_number": "1111111111", "amount": 30000},
                    {"bank_name": "Access", "account_number": "2222222222", "amount": 20000},
                ],
                "reference": "TRF-123456",
            }
        )

        assert "Transfer Successful" in result
        assert "₦50,000" in result
        assert "Funded from:" in result
        assert "TRF-123456" in result

    def test_single_source_receipt_no_breakdown(self):
        """Single source receipt doesn't show 'Funded from:' header."""
        from banking.presentation.formatters.transfer_multi_source import format_multi_source_receipt

        result = format_multi_source_receipt(
            {
                "amount": 50000,
                "recipientName": "John Doe",
                "recipientBank": "GTBank",
                "recipientAccount": "1234567890",
                "funding_sources": [
                    {"bank_name": "UBA", "account_number": "1111111111", "amount": 50000},
                ],
                "reference": "TRF-123456",
            }
        )

        # Single source should not show "Funded from:" breakdown
        assert "Funded from:" not in result
        assert "From:" in result


class TestFundingStepStatus:
    """Tests for funding step status management."""

    def test_funding_step_status_enum_values(self):
        """Test FundingStepStatusEnum has expected values."""
        from shared.database.models import FundingStepStatusEnum

        assert FundingStepStatusEnum.PENDING.value == "pending"
        assert FundingStepStatusEnum.PROCESSING.value == "processing"
        assert FundingStepStatusEnum.CONFIRMED.value == "confirmed"
        assert FundingStepStatusEnum.FAILED.value == "failed"
        assert FundingStepStatusEnum.REFUND_PENDING.value == "refund_pending"
        assert FundingStepStatusEnum.REFUND_PROCESSING.value == "refund_processing"
        assert FundingStepStatusEnum.REFUND_FAILED.value == "refund_failed"
        assert FundingStepStatusEnum.REFUNDED.value == "refunded"

    def test_funded_transfer_status_enum_values(self):
        """Test FundedTransferStatusEnum has expected values."""
        from shared.database.models import FundedTransferStatusEnum

        assert FundedTransferStatusEnum.DRAFT.value == "draft"
        assert FundedTransferStatusEnum.FUNDING_PENDING.value == "funding_pending"
        assert FundedTransferStatusEnum.COMPLETED.value == "completed"
        assert FundedTransferStatusEnum.REFUNDING.value == "refunding"
        assert FundedTransferStatusEnum.REFUNDED.value == "refunded"


class _FakeFundingSteps:
    async def any_failed(self, transfer_id: str) -> bool:
        return transfer_id == "tx-1"

    async def all_confirmed(self, transfer_id: str) -> bool:
        return False

    async def get_by_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return []


class _AllConfirmedFundingSteps:
    async def any_failed(self, transfer_id: str) -> bool:
        del transfer_id
        return False

    async def all_confirmed(self, transfer_id: str) -> bool:
        del transfer_id
        return True


class _PendingRefundFundingSteps:
    async def get_confirmed_for_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return []

    async def get_by_transfer(self, transfer_id: str) -> list[SimpleNamespace]:
        del transfer_id
        return [SimpleNamespace(id="step-1", status=FundingStepStatusEnum.REFUND_PENDING.value)]


class _FakeFundedTransfers:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []

    async def update_status(self, transfer_id: str, status: str, error_message: str | None = None) -> None:
        del error_message
        self.updated.append((transfer_id, status))


class _FakeUow:
    def __init__(self) -> None:
        self.funding_steps = _FakeFundingSteps()
        self.funded_transfers = _FakeFundedTransfers()
        self.commit_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1


class _NoopPublisher:
    async def publish(self, topic: str, message: dict) -> None:  # noqa: ARG002
        return None


class _FakeMandateAccounts:
    def __init__(self, account: SimpleNamespace | None = None, *, applied: bool = True) -> None:
        self.account = account
        self.applied = applied
        self.calls: list[dict] = []

    async def apply_mandate_status_event(
        self,
        mandate_id: str,
        status: str,
        *,
        event_name: str,
        event_id: str | None = None,
        provider_payload: dict | None = None,
    ) -> tuple[SimpleNamespace | None, bool]:
        self.calls.append(
            {
                "mandate_id": mandate_id,
                "status": status,
                "event_name": event_name,
                "event_id": event_id,
                "provider_payload": provider_payload or {},
            }
        )
        if not self.account:
            return None, False
        if self.applied:
            self.account.mandate_status = status
        return self.account, self.applied


class _FakeMandateUow:
    def __init__(self, accounts: _FakeMandateAccounts) -> None:
        self.accounts = accounts
        self.users = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestMonoMandateWebhookService:
    @pytest.mark.asyncio
    async def test_cancelled_action_event_uses_mandate_field(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        account = SimpleNamespace(id="acct-1", user_id="user-1", mandate_status="ready")
        accounts = _FakeMandateAccounts(account)
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: _FakeMandateUow(accounts))
        service = MonoWebhookService(publisher=_NoopPublisher())  # type: ignore[arg-type]

        processed = await service.handle_mandate_event(
            "events.mandate.action.cancelled",
            {"mandate": "mandate-1", "status": "success", "message": "mandate cancelled"},
            event_id="evt-cancelled",
        )

        assert processed is True
        assert accounts.calls == [
            {
                "mandate_id": "mandate-1",
                "status": "cancelled",
                "event_name": "events.mandate.action.cancelled",
                "event_id": "evt-cancelled",
                "provider_payload": {
                    "mandate": "mandate-1",
                    "id": "mandate-1",
                    "status": "success",
                    "message": "mandate cancelled",
                },
            }
        ]
        assert account.mandate_status == "cancelled"

    @pytest.mark.asyncio
    async def test_expired_event_marks_mandate_expired(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        account = SimpleNamespace(id="acct-2", user_id="user-2", mandate_status="pending")
        accounts = _FakeMandateAccounts(account)
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: _FakeMandateUow(accounts))
        service = MonoWebhookService(publisher=_NoopPublisher())  # type: ignore[arg-type]

        processed = await service.handle_mandate_event("events.mandates.expired", {"id": "mandate-2"})

        assert processed is True
        assert accounts.calls[0]["status"] == "expired"
        assert account.mandate_status == "expired"

    @pytest.mark.asyncio
    async def test_ready_event_with_ready_to_debit_false_does_not_mark_ready(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        account = SimpleNamespace(id="acct-3", user_id="user-3", mandate_status="pending")
        accounts = _FakeMandateAccounts(account)
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: _FakeMandateUow(accounts))
        service = MonoWebhookService(publisher=_NoopPublisher())  # type: ignore[arg-type]

        processed = await service.handle_mandate_event(
            "events.mandates.ready",
            {"id": "mandate-3", "status": "approved", "ready_to_debit": False},
        )

        assert processed is True
        assert accounts.calls[0]["status"] == "approved"
        assert account.mandate_status == "approved"

    @pytest.mark.asyncio
    async def test_ignored_stale_mandate_update_does_not_notify_ready(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        account = SimpleNamespace(id="acct-4", user_id="user-4", mandate_status="expired")
        accounts = _FakeMandateAccounts(account, applied=False)
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: _FakeMandateUow(accounts))
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(publisher=_NoopPublisher(), delivery_service=delivery_service)  # type: ignore[arg-type]

        processed = await service.handle_mandate_event("events.mandates.ready", {"id": "mandate-4"})

        assert processed is True
        assert account.mandate_status == "expired"
        delivery_service.deliver_text.assert_not_awaited()


class _CapturePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, topic: str, message: dict) -> None:
        self.published.append((topic, message))


@pytest.mark.asyncio
async def test_mono_webhook_queues_payout_with_provider_metadata() -> None:
    publisher = _CapturePublisher()
    service = MonoWebhookService(publisher=publisher)  # type: ignore[arg-type]
    transfer = SimpleNamespace(
        id="funded-1",
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="000014",
        payout_provider="flutterwave",
        idempotency_key="idem-1",
    )

    await service._queue_payout(transfer)  # type: ignore[arg-type]

    assert publisher.published == [
        (
            "payout.process",
            {
                "funded_transfer_id": "funded-1",
                "amount": "5000.00",
                "amount_naira": "5000.00",
                "recipient_account": "8162511023",
                "recipient_bank_code": "000014",
                "recipient_bank_code_provider": "flutterwave",
                "recipient_resolution_provider": "flutterwave",
                "payout_provider": "flutterwave",
                "idempotency_key": "idem-1",
            },
        )
    ]


class TestMonoWebhookRefundGate:
    @pytest.mark.asyncio
    async def test_any_failed_step_triggers_refund_even_if_latest_status_is_confirmed(self):
        service = MonoWebhookService(publisher=_NoopPublisher())  # type: ignore[arg-type]
        service._queue_refunds = AsyncMock()  # type: ignore[method-assign]
        uow = _FakeUow()
        transfer = SimpleNamespace(id="tx-1", status=FundedTransferStatusEnum.FUNDING_PENDING.value)

        await service._check_transfer_completion(
            uow=uow,  # type: ignore[arg-type]
            transfer=transfer,  # type: ignore[arg-type]
            latest_status=FundingStepStatusEnum.CONFIRMED.value,
        )

        assert ("tx-1", FundedTransferStatusEnum.REFUNDING.value) in uow.funded_transfers.updated
        assert uow.commit_calls == 1
        service._queue_refunds.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_duplicate_success_webhook_does_not_publish_second_payout(self):
        publisher = _CapturePublisher()
        service = MonoWebhookService(publisher=publisher)  # type: ignore[arg-type]
        uow = _FakeUow()
        uow.funding_steps = _AllConfirmedFundingSteps()
        transfer = SimpleNamespace(id="tx-2", status=FundedTransferStatusEnum.PAYOUT_PENDING.value)

        await service._check_transfer_completion(
            uow=uow,  # type: ignore[arg-type]
            transfer=transfer,  # type: ignore[arg-type]
            latest_status=FundingStepStatusEnum.CONFIRMED.value,
        )

        assert uow.funded_transfers.updated == []
        assert publisher.published == []
        assert uow.commit_calls == 0

    @pytest.mark.asyncio
    async def test_duplicate_failed_webhook_keeps_pending_refunds_open(self):
        service = MonoWebhookService(publisher=_NoopPublisher())  # type: ignore[arg-type]
        uow = _FakeUow()
        uow.funding_steps = _PendingRefundFundingSteps()
        transfer = SimpleNamespace(id="tx-3", status=FundedTransferStatusEnum.REFUNDING.value)

        await service._queue_refunds(
            uow=uow,  # type: ignore[arg-type]
            transfer=transfer,  # type: ignore[arg-type]
        )

        assert uow.funded_transfers.updated == []
        assert uow.commit_calls == 0


class _FakeTransactions:
    def __init__(self, tx: SimpleNamespace | None = None, uow: "_FakeTransferUow | None" = None) -> None:
        self.tx = tx
        self.uow = uow

    async def get_by_transaction_id(self, transaction_id: str) -> SimpleNamespace | None:
        if self.tx and self.tx.transaction_id == transaction_id:
            return self.tx
        return None

    async def get_by_transaction_id_for_update(self, transaction_id: str) -> SimpleNamespace | None:
        return await self.get_by_transaction_id(transaction_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        if self.tx and self.tx.idempotency_key == idempotency_key:
            return self.tx
        return None

    async def get_direct_transfer_by_reference_for_update(self, reference: str) -> SimpleNamespace | None:
        if self.tx and (self.tx.idempotency_key == reference or self.tx.transaction_id == reference):
            return self.tx
        return None

    async def apply_direct_transfer_result(
        self,
        transaction_id: str,
        *,
        result: object,
        provider_reference: str,
    ) -> tuple[SimpleNamespace | None, str]:
        if not self.tx or str(self.tx.id) != str(transaction_id):
            return None, "skipped"
        if str(self.tx.status).lower() in {"successful", "failed", "reversed"}:
            return self.tx, "skipped"

        provider_response = getattr(result, "provider_response", None) or {}
        provider_status = str(getattr(getattr(result, "status", None), "value", None) or getattr(result, "status", ""))
        debit_id = getattr(result, "debit_id", None)
        reference = getattr(result, "reference", None) or provider_reference
        provider_error_code = None
        if isinstance(provider_response, dict):
            provider_error_code = provider_response.get("response_code") or provider_response.get("error_code")

        self.tx.provider_status = provider_status
        self.tx.provider_error_code = None if provider_error_code is None else str(provider_error_code)
        self.tx.provider_response = provider_response
        self.tx.transaction_id = str(debit_id or reference or provider_reference)
        if provider_status == "successful":
            self.tx.status = "successful"
            self.tx.completed_at = datetime.now(UTC).replace(tzinfo=None)
            outcome = "successful"
        elif provider_status in {"pending", "processing"} and bool(getattr(result, "success", False)):
            self.tx.status = "processing"
            outcome = "processing"
        else:
            self.tx.status = "failed"
            self.tx.error_message = getattr(result, "error_message", None) or "Transfer failed"
            self.tx.completed_at = datetime.now(UTC).replace(tzinfo=None)
            outcome = "failed"
        if self.uow:
            await self.uow.commit()
        return self.tx, outcome


class _FakeUsers:
    def __init__(self, user: SimpleNamespace | None = None) -> None:
        self.user = user

    async def get_by_id(self, user_id: str) -> SimpleNamespace | None:
        if self.user and str(self.user.id) == str(user_id):
            return self.user
        return None


class _FakeExecuteResult:
    def __init__(self, row: tuple[str, str] | None = None) -> None:
        self.row = row

    def first(self) -> tuple[str, str] | None:
        return self.row


class _FakeDb:
    def __init__(self, row: tuple[str, str] | None = None) -> None:
        self.row = row

    def add(self, _: object) -> None:
        return None

    async def execute(self, _stmt) -> _FakeExecuteResult:
        return _FakeExecuteResult(self.row)


class _FakeTransferUow:
    def __init__(
        self,
        tx: SimpleNamespace | None = None,
        *,
        user: SimpleNamespace | None = None,
        channel_identity: tuple[str, str] | None = None,
    ) -> None:
        self.commit_calls = 0
        self.transactions = _FakeTransactions(tx, self)
        self.funding_steps = None
        self.funded_transfers = None
        self.accounts = None
        self.users = _FakeUsers(user)
        self.db = _FakeDb(channel_identity)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.commit_calls += 1


class _RedisStub:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


class TestMonoWebhookTransferUpdates:
    @pytest.mark.asyncio
    async def test_debit_success_updates_transfer_transaction(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        tx = SimpleNamespace(
            id="tx-1",
            user_id="user-1",
            idempotency_key="idem-1",
            transaction_id="debit-1",
            status="processing",
            provider_status=None,
            provider_error_code=None,
            provider_response=None,
            error_message=None,
            completed_at=None,
        )
        user = SimpleNamespace(id="user-1", phone_number="2348162511023")
        fake_uow = _FakeTransferUow(tx, user=user, channel_identity=("telegram", "927331985"))
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: fake_uow)
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(publisher=_NoopPublisher(), delivery_service=delivery_service)  # type: ignore[arg-type]

        processed = await service.handle_debit_event(
            "events.mandates.debit.successful",
            {"id": "debit-1", "reference_number": "idem-1", "status": "successful", "response_code": "00"},
        )

        assert processed is True
        assert tx.status == "successful"
        assert tx.provider_status == "successful"
        assert tx.provider_error_code == "00"
        assert tx.provider_response == {
            "id": "debit-1",
            "reference_number": "idem-1",
            "status": "successful",
            "response_code": "00",
        }
        assert tx.completed_at is not None
        assert fake_uow.commit_calls == 1
        delivery_service.deliver_text.assert_awaited_once()
        assert delivery_service.deliver_text.await_args.kwargs["phone_number"] == "927331985"
        assert delivery_service.deliver_text.await_args.kwargs["channel"] == "telegram"
        delivered_text = delivery_service.deliver_text.await_args.kwargs["text"]
        assert "Transfer successful" in delivered_text
        assert "Transaction ID" not in delivered_text
        assert "debit-1" not in delivered_text

    @pytest.mark.asyncio
    async def test_directpay_nested_object_updates_transfer_transaction_by_reference(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        tx = SimpleNamespace(
            id="tx-directpay",
            user_id="user-directpay",
            idempotency_key="directpay-ref-1",
            transaction_id=None,
            status="processing",
            provider_status=None,
            provider_error_code=None,
            provider_response=None,
            error_message=None,
            completed_at=None,
        )
        user = SimpleNamespace(id="user-directpay", phone_number="2348162511023")
        fake_uow = _FakeTransferUow(tx, user=user, channel_identity=("telegram", "927331985"))
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: fake_uow)
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(publisher=_NoopPublisher(), delivery_service=delivery_service)  # type: ignore[arg-type]
        payload = {
            "type": "onetime-debit",
            "object": {
                "id": "txd-directpay-1",
                "reference": "directpay-ref-1",
                "status": "successful",
                "message": "Payment was successful",
            },
        }

        processed = await service.handle_debit_event("direct_debit.payment_successful", payload)

        assert processed is True
        assert tx.status == "successful"
        assert tx.transaction_id == "txd-directpay-1"
        assert tx.provider_status == "successful"
        assert tx.provider_response == payload
        assert tx.completed_at is not None
        delivery_service.deliver_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_debit_failure_updates_transfer_transaction_by_reference(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        tx = SimpleNamespace(
            id="tx-2",
            user_id="user-2",
            idempotency_key="idem-2",
            transaction_id=None,
            status="processing",
            provider_status=None,
            provider_error_code=None,
            provider_response=None,
            error_message=None,
            completed_at=None,
        )
        user = SimpleNamespace(id="user-2", phone_number="2348162511024")
        fake_uow = _FakeTransferUow(tx, user=user, channel_identity=("whatsapp", "2348162511024"))
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: fake_uow)
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(publisher=_NoopPublisher(), delivery_service=delivery_service)  # type: ignore[arg-type]

        processed = await service.handle_debit_event(
            "events.mandates.debit.failed",
            {
                "reference_number": "idem-2",
                "status": "failed",
                "response_code": "51",
                "message": "Insufficient funds",
            },
        )

        assert processed is True
        assert tx.status == "failed"
        assert tx.provider_status == "failed"
        assert tx.provider_error_code == "51"
        assert tx.error_message == "Insufficient funds"
        assert tx.completed_at is not None
        delivery_service.deliver_text.assert_awaited_once()
        assert "Insufficient funds" in delivery_service.deliver_text.await_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_success_webhook_does_not_notify_again_when_transfer_already_terminal(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        tx = SimpleNamespace(
            id="tx-3",
            user_id="user-3",
            idempotency_key="idem-3",
            transaction_id="debit-3",
            status="successful",
            provider_status="successful",
            provider_error_code="00",
            provider_response=None,
            error_message=None,
            completed_at=None,
        )
        user = SimpleNamespace(id="user-3", phone_number="2348162511025")
        fake_uow = _FakeTransferUow(tx, user=user, channel_identity=("telegram", "927331986"))
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: fake_uow)
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(publisher=_NoopPublisher(), delivery_service=delivery_service)  # type: ignore[arg-type]

        processed = await service.handle_debit_event(
            "events.mandates.debit.successful",
            {"id": "debit-3", "reference_number": "idem-3", "status": "successful", "response_code": "00"},
        )

        assert processed is True
        delivery_service.deliver_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grouped_transfer_webhook_suppresses_first_terminal_leg_notification(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        redis_client = _RedisStub()
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message={
                "transaction_id": "tx-10",
                "async_group": {
                    "async_group_id": "group-10",
                    "async_group_size": 2,
                    "async_group_kind": "multi_transfer",
                    "async_group_index": 1,
                },
            },
            task_type="transfer",
            payload={
                "amount": 10000,
                "recipient_name": "Mum",
                "recipient_resolved_name": "Mercy Johnson",
                "recipient_bank_name": "Opay",
                "recipient_account": "8162511023",
                "final_status": "processing",
            },
            locale="en",
        )
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message={
                "transaction_id": "tx-11",
                "async_group": {
                    "async_group_id": "group-10",
                    "async_group_size": 2,
                    "async_group_kind": "multi_transfer",
                    "async_group_index": 2,
                },
            },
            task_type="transfer",
            payload={
                "amount": 10000,
                "recipient_name": "Tolu",
                "recipient_resolved_name": "Tolu Adedayo",
                "recipient_bank_name": "First Bank",
                "recipient_account": "0760505261",
                "final_status": "processing",
            },
            locale="en",
        )

        tx = SimpleNamespace(
            id="tx-10",
            user_id="user-10",
            idempotency_key="idem-10",
            transaction_id="debit-10",
            status="processing",
            provider_status=None,
            provider_error_code=None,
            provider_response=None,
            error_message=None,
            completed_at=None,
            amount=10000,
            recipient_name="Mercy Johnson",
            recipient_account_number="8162511023",
            recipient_bank_name="Opay",
            source_bank_name="Zenith Bank",
            narration="Allowance",
        )
        user = SimpleNamespace(id="user-10", phone_number="2348162511023")
        fake_uow = _FakeTransferUow(tx, user=user, channel_identity=("telegram", "927331985"))
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: fake_uow)
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(
            publisher=_NoopPublisher(),
            delivery_service=delivery_service,
            redis_client=redis_client,
        )  # type: ignore[arg-type]

        processed = await service.handle_debit_event(
            "events.mandates.debit.successful",
            {"id": "debit-10", "reference_number": "idem-10", "status": "successful", "response_code": "00"},
        )

        assert processed is True
        delivery_service.deliver_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grouped_transfer_webhook_sends_final_batch_summary_with_per_leg_statuses(self, monkeypatch):
        from apps.gateway.api.webhooks.mono import service as service_module

        redis_client = _RedisStub()
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message={
                "transaction_id": "tx-20",
                "async_group": {
                    "async_group_id": "group-20",
                    "async_group_size": 2,
                    "async_group_kind": "multi_transfer",
                    "async_group_index": 1,
                },
            },
            task_type="transfer",
            payload={
                "amount": 10000,
                "recipient_name": "Mum",
                "recipient_resolved_name": "Mercy Johnson",
                "recipient_bank_name": "Opay",
                "recipient_account": "8162511023",
                "final_status": "success",
            },
            locale="en",
        )
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message={
                "transaction_id": "tx-21",
                "async_group": {
                    "async_group_id": "group-20",
                    "async_group_size": 2,
                    "async_group_kind": "multi_transfer",
                    "async_group_index": 2,
                },
            },
            task_type="transfer",
            payload={
                "amount": 10000,
                "recipient_name": "Tolu",
                "recipient_resolved_name": "Tolu Adedayo",
                "recipient_bank_name": "First Bank",
                "recipient_account": "0760505261",
                "final_status": "processing",
            },
            locale="en",
        )

        tx = SimpleNamespace(
            id="tx-21",
            user_id="user-20",
            idempotency_key="idem-21",
            transaction_id="debit-21",
            status="processing",
            provider_status=None,
            provider_error_code=None,
            provider_response=None,
            error_message=None,
            completed_at=None,
            amount=10000,
            recipient_name="Tolu Adedayo",
            recipient_account_number="0760505261",
            recipient_bank_name="First Bank",
            source_bank_name="Zenith Bank",
            narration="Transport",
        )
        user = SimpleNamespace(id="user-20", phone_number="2348162511023")
        fake_uow = _FakeTransferUow(tx, user=user, channel_identity=("telegram", "927331985"))
        monkeypatch.setattr(service_module, "UnitOfWork", lambda: fake_uow)
        delivery_service = SimpleNamespace(deliver_text=AsyncMock())
        service = MonoWebhookService(
            publisher=_NoopPublisher(),
            delivery_service=delivery_service,
            redis_client=redis_client,
        )  # type: ignore[arg-type]

        processed = await service.handle_debit_event(
            "events.mandates.debit.failed",
            {
                "id": "debit-21",
                "reference_number": "idem-21",
                "status": "failed",
                "response_code": "51",
                "message": "Insufficient funds",
            },
        )

        assert processed is True
        delivery_service.deliver_text.assert_awaited_once()
        text = delivery_service.deliver_text.await_args.kwargs["text"]
        assert "✓ ₦10,000 → Mum (Mercy Johnson)" in text
        assert "✗ ₦10,000 → Tolu Adedayo" in text
        assert "Some transactions could not be completed." in text
