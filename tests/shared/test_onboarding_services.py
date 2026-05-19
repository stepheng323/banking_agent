from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from apps.chat.src.agent.graphs.onboarding import service as onboarding_service_module
from shared.clients.providers.mono.models import BankAccount, BvnLookupData, BvnMethod, Institution
from shared.services.onboarding.account_add import AccountAddService
from shared.services.onboarding.account_linking import AccountLinkingService
from shared.services.onboarding.bvn_verification import BvnVerificationService
from shared.services.onboarding.session import OnboardingStep


class _SessionStub:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or {}
        self.strict_calls: list[tuple[str, dict[str, Any], bool]] = []

    async def get_session(self, flow_token: str) -> dict[str, Any]:
        del flow_token
        return self.data

    async def update_session(self, flow_token: str, updates: dict[str, Any]) -> bool:
        del flow_token
        self.data.update(updates)
        return True

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
    def __init__(self, *, user: Any = None, existing_by_id: Any = None, existing_for_user: list[Any] | None = None) -> None:
        self.users = _UserRepoStub(user)
        self.accounts = _AccountRepoStub(existing_by_id=existing_by_id, existing_for_user=existing_for_user)

    async def __aenter__(self) -> "_UnitOfWorkStub":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


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

    monkeypatch.setattr("shared.services.onboarding.account_add.UnitOfWork", lambda: uow)
    monkeypatch.setattr(
        "shared.services.onboarding.account_add.UserDataCache.invalidate_accounts",
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
    service = BvnVerificationService(session)

    async def _unexpected_lookup(bvn: str) -> BvnLookupData:
        del bvn
        raise AssertionError("BVN lookup should not run without a session-bound phone number")

    monkeypatch.setattr(
        "shared.services.onboarding.bvn_verification.mono_client.initiate_bvn_lookup",
        _unexpected_lookup,
    )

    result = await service.initiate_bvn_verification("onboarding-attacker-token", "12345678901")

    assert result == {"success": False, "error": "Session expired. Please start over."}


@pytest.mark.asyncio
async def test_bvn_verification_uses_session_phone_not_token_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionStub({"phone_number": "2348162511023"})
    service = BvnVerificationService(session)
    lookups: list[str] = []

    async def _lookup(bvn: str) -> BvnLookupData:
        lookups.append(bvn)
        return BvnLookupData(
            session_id="mono-session-1",
            bvn=bvn,
            methods=[BvnMethod(method="sms", hint="081***1023")],
        )

    monkeypatch.setattr("shared.services.onboarding.bvn_verification.mono_client.initiate_bvn_lookup", _lookup)

    result = await service.initiate_bvn_verification("onboarding-random-suffix-9999999999", "12345678901")

    assert result == {
        "success": True,
        "data": {"bvn": "12345678901", "methods": [{"id": "sms", "title": "081***1023"}]},
    }
    assert lookups == ["12345678901"]
    assert session.data["phone_number"] == "2348162511023"
    assert session.data["is_account_linking"] is False
    assert session.data["step"] == OnboardingStep.METHOD_SELECTION.value


@pytest.mark.asyncio
async def test_telegram_complete_onboarding_requires_session_identity() -> None:
    session = _SessionStub({"phone_number": "2348162511023"})
    service = AccountLinkingService(session, _MandateStub())

    result = await service.complete_onboarding(
        "onboarding-legacy-chat-id-12345",
        pin="1234",
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
    service = BvnVerificationService(session)
    user = SimpleNamespace(id=uuid4())
    existing_account = SimpleNamespace(account_id="058_8162511022")
    uow = _UnitOfWorkStub(user=user, existing_for_user=[existing_account])

    async def _verify_otp(session_id: str, otp: str) -> list[BankAccount]:
        del session_id, otp
        return [
            BankAccount(
                account_name="Existing Account",
                account_number="8162511022",
                account_type="savings",
                institution=Institution(name="Access Bank", bank_code="058"),
            ),
            BankAccount(
                account_name="New Account",
                account_number="0334555167",
                account_type="savings",
                institution=Institution(name="GTBank", bank_code="058"),
            ),
        ]

    monkeypatch.setattr("shared.services.onboarding.bvn_verification.UnitOfWork", lambda: uow)
    monkeypatch.setattr("shared.services.onboarding.bvn_verification.mono_client.verify_otp", _verify_otp)

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
    service = BvnVerificationService(session)
    user = SimpleNamespace(id=uuid4())
    existing_account = SimpleNamespace(account_id="058_8162511022")
    uow = _UnitOfWorkStub(user=user, existing_for_user=[existing_account])

    async def _verify_otp(session_id: str, otp: str) -> list[BankAccount]:
        del session_id, otp
        return [
            BankAccount(
                account_name="Existing Account",
                account_number="8162511022",
                account_type="savings",
                institution=Institution(name="Access Bank", bank_code="058"),
            )
        ]

    monkeypatch.setattr("shared.services.onboarding.bvn_verification.UnitOfWork", lambda: uow)
    monkeypatch.setattr("shared.services.onboarding.bvn_verification.mono_client.verify_otp", _verify_otp)

    result = await service.verify_otp("link-token", "123456")

    assert result == {"success": False, "error": "No new accounts available to link."}
    assert session.data.get("accounts") is None
    assert session.data.get("step") is None
