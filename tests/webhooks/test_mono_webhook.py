"""Tests for Mono webhook handler - Unit tests with mocked dependencies."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.gateway.api.webhooks.mono.service import MonoWebhookService
from shared.database.enums import FundedTransferStatusEnum, FundingStepStatusEnum
from shared.services.async_completion import record_group_leg_and_maybe_build_summary


class _FakeMonoRequest:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    async def json(self) -> dict:
        return self._payload


class _RouterServiceStub:
    MANDATE_STATUS_MAP = {"events.mandates.approved": "approved"}
    DEBIT_STATUS_MAP = {"events.mandates.debit.successful": "confirmed"}

    def __init__(self) -> None:
        self.mandate_events: list[tuple[str, dict]] = []
        self.debit_events: list[tuple[str, dict]] = []

    async def handle_mandate_event(self, event: str, data: dict) -> bool:
        self.mandate_events.append((event, data))
        return True

    async def handle_debit_event(self, event: str, data: dict) -> bool:
        self.debit_events.append((event, data))
        return True


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
        from shared.formatters.transfer import format_multi_source_transfer_summary

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
        from shared.formatters.transfer import format_multi_source_receipt

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
        from shared.formatters.transfer import format_multi_source_receipt

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


class _FakeFundedTransfers:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []

    async def update_status(self, transfer_id: str, status: str) -> None:
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


class _FakeTransactions:
    def __init__(self, tx: SimpleNamespace | None = None) -> None:
        self.tx = tx

    async def get_by_transaction_id(self, transaction_id: str) -> SimpleNamespace | None:
        if self.tx and self.tx.transaction_id == transaction_id:
            return self.tx
        return None

    async def get_by_idempotency_key(self, idempotency_key: str) -> SimpleNamespace | None:
        if self.tx and self.tx.idempotency_key == idempotency_key:
            return self.tx
        return None


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
        self.transactions = _FakeTransactions(tx)
        self.funding_steps = None
        self.funded_transfers = None
        self.accounts = None
        self.users = _FakeUsers(user)
        self.db = _FakeDb(channel_identity)
        self.commit_calls = 0

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
        assert "Transfer successful" in delivery_service.deliver_text.await_args.kwargs["text"]

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
        assert "Some transactions completed, but others failed." in text
