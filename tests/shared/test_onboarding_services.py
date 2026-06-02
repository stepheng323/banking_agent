from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from apps.chat.src.agent.workers.onboarding import service as onboarding_service_module
from banking.accounts.onboarding.account_add import AccountAddService
from banking.accounts.onboarding.account_linking import AccountLinkingService
from banking.accounts.onboarding.bvn_verification import BvnVerificationService
from banking.accounts.onboarding.session import OnboardingStep
from shared.cache.flow_session_manager import SessionReadResult
from shared.clients.abstractions.banking import BvnLookupResult, BvnVerificationResult


class _BankingProviderStub:
    def __init__(
        self,
        *,
        lookup_result: BvnLookupResult | None = None,
        verify_bvn_result: dict[str, Any] | None = None,
        otp_result: BvnVerificationResult | None = None,
    ) -> None:
        self.lookup_result = lookup_result
        self.verify_bvn_result = verify_bvn_result or {"success": True}
        self.otp_result = otp_result
        self.lookup_calls: list[str] = []
        self.verify_bvn_calls: list[tuple[str, str]] = []
        self.verify_otp_calls: list[tuple[str, str]] = []

    @property
    def provider_name(self) -> str:
        return "stub"

    @property
    def is_available(self) -> bool:
        return True

    async def initiate_bvn_lookup(self, bvn: str) -> BvnLookupResult:
        self.lookup_calls.append(bvn)
        if self.lookup_result is None:
            raise AssertionError("BVN lookup should not run")
        return self.lookup_result

    async def verify_bvn(self, session_id: str, method: str) -> dict[str, Any]:
        self.verify_bvn_calls.append((session_id, method))
        return self.verify_bvn_result

    async def verify_otp(self, session_id: str, otp: str) -> BvnVerificationResult:
        self.verify_otp_calls.append((session_id, otp))
        if self.otp_result is None:
            raise AssertionError("OTP verification should not run")
        return self.otp_result


class _SessionStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or {}
        self.strict_calls: list[tuple[str, dict[str, Any], bool]] = []

    async def read_session(self, flow_token: str) -> SessionReadResult:
        del flow_token
        if not self.data:
            return SessionReadResult(status="missing")
        return SessionReadResult(status="found", data=self.data)

    async def update_session_strict(
        self,
        flow_token: str,
        updates: dict[str, Any],
        *,
        verify: bool = False,
    ) -> bool:
        self.strict_calls.append((flow_token, updates, verify))
        self.data.update(updates)
        return True


class _MandateStub:
    def __init__(self) -> None:
        self.sent_auth_calls: list[dict[str, Any]] = []

    async def create_mandate(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise AssertionError("create_mandate should not run in this test")

    async def send_auth_instructions(self, **kwargs: Any) -> None:
        self.sent_auth_calls.append(kwargs)

    async def enqueue_outbox_say(self, phone_number: str, msg: str) -> None:
        del phone_number, msg
        raise AssertionError("enqueue_outbox_say should not run in this test")


class _UserRepoStub:
    def __init__(self, user: Any) -> None:
        self.user = user

    async def get_by_phone(self, phone_number: str) -> Any:
        del phone_number
        return self.user


class _AccountRepoStub:
    def __init__(self, *, existing_by_id: Any = None, existing_for_user: list[Any] | None = None) -> None:
        self.existing_by_id = existing_by_id
        self.existing_for_user = existing_for_user or []
        self.created: list[Any] = []

    async def get_by_account_id(self, account_id: str) -> Any:
        del account_id
        return self.existing_by_id

    async def create_account(self, create_account: Any) -> Any:
        self.created.append(create_account)
        return create_account

    async def get_by_user(self, user_id: str) -> list[Any]:
        del user_id
        return self.existing_for_user


class _UnitOfWorkStub:
    def __init__(
        self, *, user: Any = None, existing_by_id: Any = None, existing_for_user: list[Any] | None = None
    ) -> None:
        self.users = _UserRepoStub(user)
        self.accounts = _AccountRepoStub(existing_by_id=existing_by_id, existing_for_user=existing_for_user)

    async def __aenter__(self) -> "_UnitOfWorkStub":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


class _LoggerStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


@pytest.mark.asyncio
async def test_onboarding_service_generates_opaque_token_and_seeds_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub()
    captured: dict[str, Any] = {}

    async def _enqueue_outbox_intents(
        publisher: Any,
        phone_number: str,
        channel: str,
        intents: list[Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        captured.update(
            {
                "publisher": publisher,
                "phone_number": phone_number,
                "channel": channel,
                "intents": intents,
                "metadata": metadata,
            }
        )

    monkeypatch.setattr(onboarding_service_module.secrets, "token_urlsafe", lambda _: "opaque-token")
    monkeypatch.setattr(onboarding_service_module, "enqueue_outbox_intents", _enqueue_outbox_intents)

    publisher = object()
    service = onboarding_service_module.OnboardingService(publisher=publisher, session_manager=session)  # type: ignore[arg-type]

    await service.send_onboarding_flow("2348162511023", channel="whatsapp")

    assert session.strict_calls == [
        (
            "onboarding-opaque-token",
            {
                "phone_number": "2348162511023",
                "channel": "whatsapp",
                "channel_user_id": "2348162511023",
                "step": OnboardingStep.BVN_ENTRY.value,
            },
            True,
        )
    ]
    assert captured["publisher"] is publisher
    assert captured["phone_number"] == "2348162511023"
    assert captured["channel"] == "whatsapp"
    assert captured["metadata"] == {"source": "onboarding"}
    [intent] = captured["intents"]
    assert intent.flow_config["flow_token"] == "onboarding-opaque-token"
    assert "2348162511023" not in intent.flow_config["flow_token"]


@pytest.mark.asyncio
async def test_account_add_service_uses_async_uow_and_creates_account(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _SessionStub(
        {
            "phone_number": "2348162511023",
            "channel": "telegram",
            "accounts": [
                {
                    "id": "058_8162511022",
                    "account_number": "8162511022",
                    "bank_name": "Access Bank",
                    "bank_code": "058",
                    "account_name": "Gaines Test",
                }
            ],
        }
    )
    user = SimpleNamespace(id=uuid4(), mono_customer_id=None)
    uow = _UnitOfWorkStub(user=user)
    invalidated: list[str] = []

    async def _invalidate_accounts(_self: Any, phone_number: str) -> None:
        invalidated.append(phone_number)

    monkeypatch.setattr("banking.accounts.onboarding.account_add.UnitOfWork", lambda: uow)
    monkeypatch.setattr(
        "banking.accounts.onboarding.account_add.UserDataCache.invalidate_accounts",
        _invalidate_accounts,
    )

    service = AccountAddService(session, _MandateStub())
    result = await service.add_account("link-token", "058_8162511022")

    assert result["success"] is True
    assert session.data["step"] == "complete"
    assert len(uow.accounts.created) == 1
    created = uow.accounts.created[0]
    assert created.account_id == "058_8162511022"
    assert created.account_number == "8162511022"
    assert created.bank_name == "Access Bank"
    assert invalidated == ["2348162511023"]


@pytest.mark.asyncio
async def test_bvn_verification_rejects_missing_preseeded_phone_without_calling_mono(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub({})
    banking_provider = _BankingProviderStub()
    service = BvnVerificationService(session, banking_provider=banking_provider)

    result = await service.initiate_bvn_verification("onboarding-attacker-token", "12345678901")

    assert result == {"success": False, "error": "Session expired. Please start over."}
    assert banking_provider.lookup_calls == []


@pytest.mark.asyncio
async def test_bvn_verification_uses_session_phone_not_token_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub({"phone_number": "2348162511023"})
    banking_provider = _BankingProviderStub(
        lookup_result=BvnLookupResult(
            success=True,
            session_id="mono-session-1",
            bvn="12345678901",
            verification_methods=[{"method": "sms", "hint": "081***1023"}],
        )
    )
    service = BvnVerificationService(session, banking_provider=banking_provider)

    result = await service.initiate_bvn_verification("onboarding-random-suffix-9999999999", "12345678901")

    assert result == {
        "success": True,
        "data": {"bvn": "12345678901", "methods": [{"id": "sms", "title": "081***1023"}]},
    }
    assert banking_provider.lookup_calls == ["12345678901"]
    assert session.data["phone_number"] == "2348162511023"
    assert session.data["is_account_linking"] is False
    assert session.data["step"] == OnboardingStep.METHOD_SELECTION.value


@pytest.mark.asyncio
async def test_send_otp_logs_redacted_session_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _SessionStub(
        {
            "phone_number": "2348162511023",
            "bvn": "12345678901",
            "session_id": "mono-session-secret",
            "methods": [{"id": "sms", "title": "081***1023"}],
            "accounts": [{"account_number": "8162511022"}],
            "step": OnboardingStep.METHOD_SELECTION.value,
        }
    )
    banking_provider = _BankingProviderStub()
    service = BvnVerificationService(session, banking_provider=banking_provider)
    logger = _LoggerStub()

    monkeypatch.setattr("banking.accounts.onboarding.bvn_verification.logger", logger)

    result = await service.send_otp("flow-token-secret", "sms")

    assert result == {"success": True, "data": {"bvn": "12345678901"}}
    assert banking_provider.verify_bvn_calls == [("mono-session-secret", "sms")]

    session_events = [fields for event, fields in logger.events if event == "otp_session_loaded"]
    assert session_events == [
        {
            "flow_token_hash": "82a39dc852becb79",
            "step": OnboardingStep.METHOD_SELECTION.value,
            "phone_masked": "2348***23",
            "has_bvn": True,
            "has_session_id": True,
            "has_accounts": True,
        }
    ]

    serialized_logs = str(logger.events)
    assert "12345678901" not in serialized_logs
    assert "mono-session-secret" not in serialized_logs
    assert "8162511022" not in serialized_logs
    assert "2348162511023" not in serialized_logs
    assert "flow-token-secret" not in serialized_logs


@pytest.mark.asyncio
async def test_complete_onboarding_rejects_four_digit_pin() -> None:
    service = AccountLinkingService(_SessionStub(), _MandateStub())

    result = await service.complete_onboarding(
        "onboarding-token",
        pin="1234",
        email="gaines@example.com",
        address="1 Marina Road",
    )

    assert result == {"success": False, "error": "Invalid PIN. Please enter a 6-digit numeric PIN."}


@pytest.mark.asyncio
async def test_telegram_complete_onboarding_requires_session_identity() -> None:
    session = _SessionStub({"phone_number": "2348162511023"})
    service = AccountLinkingService(session, _MandateStub())

    result = await service.complete_onboarding(
        "onboarding-legacy-chat-id-12345",
        pin="123456",
        email="gaines@example.com",
        address="1 Marina Road",
        channel="telegram",
    )

    assert result == {"success": False, "error": "Telegram session identity missing."}


@pytest.mark.asyncio
async def test_account_add_service_setup_mandate_uses_originating_channel() -> None:
    mandate = _MandateStub()
    service = AccountAddService(_SessionStub(), mandate)

    async def _create_mandate(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "success": True,
            "mandate": SimpleNamespace(
                transfer_destinations=[SimpleNamespace(bank_name="NIBSS Bank", account_number="0001112223")]
            ),
        }

    mandate.create_mandate = _create_mandate

    await service._setup_mandate_for_account(
        phone_number="2348162511023",
        mono_customer_id="mono-customer-1",
        account_id="058_8162511022",
        account_number="8162511022",
        bank_code="058",
        bank_name="Access Bank",
        channel="telegram",
    )

    assert mandate.sent_auth_calls == [
        {
            "phone_number": "2348162511023",
            "account_number": "8162511022",
            "bank_name": "Access Bank",
            "transfer_destinations": [SimpleNamespace(bank_name="NIBSS Bank", account_number="0001112223")],
            "channel": "telegram",
        }
    ]


@pytest.mark.asyncio
async def test_verify_otp_filters_out_existing_linked_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _SessionStub({"session_id": "mono-session", "phone_number": "2348162511023", "bvn": "12345678901"})
    banking_provider = _BankingProviderStub(
        otp_result=BvnVerificationResult(
            success=True,
            accounts=[
                {
                    "account_name": "Existing Account",
                    "account_number": "8162511022",
                    "account_type": "savings",
                    "bank_name": "Access Bank",
                    "bank_code": "058",
                },
                {
                    "account_name": "New Account",
                    "account_number": "0334555167",
                    "account_type": "savings",
                    "bank_name": "GTBank",
                    "bank_code": "058",
                },
            ],
        )
    )
    service = BvnVerificationService(session, banking_provider=banking_provider)
    user = SimpleNamespace(id=uuid4())
    existing_account = SimpleNamespace(account_id="058_8162511022")
    uow = _UnitOfWorkStub(user=user, existing_for_user=[existing_account])

    monkeypatch.setattr("banking.accounts.onboarding.bvn_verification.UnitOfWork", lambda: uow)

    result = await service.verify_otp("link-token", "123456")

    assert result["success"] is True
    assert result["data"]["accounts"] == [{"id": "058_0334555167", "title": "GTBank - 0334555167"}]
    assert session.data["accounts"] == [
        {
            "id": "058_0334555167",
            "account_number": "0334555167",
            "bank_name": "GTBank",
            "bank_code": "058",
            "account_name": "New Account",
            "account_type": "savings",
        }
    ]


@pytest.mark.asyncio
async def test_verify_otp_returns_error_when_all_accounts_are_already_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub({"session_id": "mono-session", "phone_number": "2348162511023", "bvn": "12345678901"})
    banking_provider = _BankingProviderStub(
        otp_result=BvnVerificationResult(
            success=True,
            accounts=[
                {
                    "account_name": "Existing Account",
                    "account_number": "8162511022",
                    "account_type": "savings",
                    "bank_name": "Access Bank",
                    "bank_code": "058",
                }
            ],
        )
    )
    service = BvnVerificationService(session, banking_provider=banking_provider)
    user = SimpleNamespace(id=uuid4())
    existing_account = SimpleNamespace(account_id="058_8162511022")
    uow = _UnitOfWorkStub(user=user, existing_for_user=[existing_account])

    monkeypatch.setattr("banking.accounts.onboarding.bvn_verification.UnitOfWork", lambda: uow)

    result = await service.verify_otp("link-token", "123456")

    assert result == {"success": False, "error": "No new accounts available to link."}
    assert session.data.get("accounts") is None
    assert session.data.get("step") is None
