from typing import Any

from banking.accounts.management.serialization import serialize_accounts
from banking.accounts.management.worker import AccountWorker
from banking.runtime.results import AccountOutcome


class _StructuredLLM:
    async def ainvoke(self, _messages: Any) -> dict[str, Any]:
        return {"action": "count", "identifier": None, "language": "english"}


class _DummyLLM:
    def with_structured_output(self, _schema: Any) -> Any:
        return _StructuredLLM()


class _DummyRepo:
    async def get_by_user(self, _user_id: str) -> list[Any]:
        return []

    async def get_by_id(self, _user_id: str) -> Any | None:
        return None


class _DummyBankingProvider:
    async def get_balance(self, _account_id: str) -> None:
        return None


async def test_account_worker_answers_count_question() -> None:
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_DummyBankingProvider(),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={"action": "list_accounts"},
        context={
            "profile": {"id": "u_1"},
            "language": "en",
            "accounts": [
                {"id": "a1", "bank_name": "First", "account_number": "0001"},
                {"id": "a2", "bank_name": "GTB", "account_number": "0002"},
                {"id": "a3", "bank_name": "Access", "account_number": "0003"},
            ],
        },
        user_message="How many accounts do I have?",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response == "You have 3 linked accounts."


def test_account_worker_serializes_dict_accounts_for_context_frames() -> None:
    serialized = serialize_accounts(
        [
            {
                "id": "a1",
                "bank_name": "Zenith Bank",
                "account_number": "1234509384",
                "mandate_status": "pending",
                "is_default": False,
            },
            {
                "id": "a2",
                "bank_name": "GTBank",
                "account_number": "6000000002",
                "mandate_status": "ready",
                "is_default": True,
            },
        ]
    )

    assert serialized[0]["bank_name"] == "Zenith Bank"
    assert serialized[0]["account_number"] == "1234509384"
    assert serialized[0]["mandate_status"] == "pending"
    assert serialized[1]["bank_name"] == "GTBank"
    assert serialized[1]["account_number"] == "6000000002"
    assert serialized[1]["is_default"] is True
