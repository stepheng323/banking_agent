from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from banking.accounts.management.serialization import serialize_accounts
from banking.accounts.management.worker import AccountWorker
from banking.runtime.results import AccountOutcome
from shared.types.balance import BalanceQueryContract
from shared.types.conversation_sets import AccountLifecycleContract
from shared.types.read import ReadRequest


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


class _AccountsRepo:
    def __init__(self, accounts: list[Any]) -> None:
        self.accounts = accounts

    async def get_by_user(self, _user_id: str) -> list[Any]:
        return self.accounts


class _BalanceProvider:
    async def get_balance(self, account_id: str) -> SimpleNamespace:
        del account_id
        return SimpleNamespace(available_balance=Decimal("30000.00"), currency="NGN")


class _MappedBalanceProvider:
    def __init__(self, amounts: dict[str, Decimal]) -> None:
        self.amounts = amounts

    async def get_balance(self, account_id: str) -> SimpleNamespace:
        return SimpleNamespace(available_balance=self.amounts[account_id], currency="NGN")


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
        payload={
            "action": "count",
            "read_request": ReadRequest(subject="linked_account", response_shape="fact_count").model_dump(mode="json"),
            "account_lifecycle_contract": AccountLifecycleContract(
                operation="count", response_shape="fact_count"
            ).model_dump(mode="json"),
        },
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


async def test_account_worker_zero_count_uses_natural_copy() -> None:
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_DummyBankingProvider(),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={
            "action": "count",
            "read_request": ReadRequest(subject="linked_account", response_shape="fact_count").model_dump(mode="json"),
            "account_lifecycle_contract": AccountLifecycleContract(
                operation="count", response_shape="fact_count"
            ).model_dump(mode="json"),
        },
        context={"profile": {"id": "u_1"}, "language": "en", "accounts": []},
        user_message="How many accounts do I have?",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response == "You don't have any linked accounts."
    assert " 0 " not in f" {result.response} "


async def test_account_worker_keeps_list_shape_distinct_from_count() -> None:
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_DummyBankingProvider(),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={
            "action": "list_accounts",
            "read_request": {"subject": "linked_account", "response_shape": "surface_list"},
            "account_lifecycle_contract": {"operation": "list", "response_shape": "surface_list"},
        },
        context={
            "profile": {"id": "u_1"},
            "language": "en",
            "accounts": [
                {"id": "a1", "bank_name": "First", "account_number": "0001"},
                {"id": "a2", "bank_name": "GTB", "account_number": "0002"},
            ],
        },
        user_message="Show my accounts",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response is not None
    assert result.response.startswith("Your Bank Accounts")
    assert result.outbox
    assert result.outbox[0]["body_blocks"][0] == {"type": "heading", "text": "Your Bank Accounts"}
    assert "You have 2 linked accounts." not in result.response


async def test_account_worker_answers_typed_default_account_read_without_reparsing() -> None:
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_DummyBankingProvider(),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={
            "action": "get_default",
            "read_request": ReadRequest(subject="default_account", response_shape="fact_value").model_dump(mode="json"),
            "account_lifecycle_contract": AccountLifecycleContract(
                operation="default_identity", response_shape="fact_value"
            ).model_dump(mode="json"),
        },
        context={
            "profile": {"id": "u_1"},
            "language": "en",
            "accounts": [
                {"id": "a1", "bank_name": "First Bank", "account_number": "0000000001"},
                {
                    "id": "a2",
                    "bank_name": "GTBank",
                    "account_number": "2010000002",
                    "is_default": True,
                },
            ],
        },
        user_message="What's my default account?",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response == "Your default account is GTBank (···0002)."
    assert result.patch["account_lifecycle_contract"]["operation"] == "default_identity"


async def test_account_worker_balance_returns_mobile_body_blocks() -> None:
    worker = AccountWorker(
        account_repo=_AccountsRepo(
            [
                SimpleNamespace(account_id="acc_1", bank_name="Access Bank", account_number="1234560003"),
                SimpleNamespace(account_id="acc_2", bank_name="GTBank", account_number="1234560002"),
            ]
        ),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_BalanceProvider(),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={
            "action": "check_balance",
            "read_request": ReadRequest(subject="balance", response_shape="surface_list").model_dump(mode="json"),
            "balance_contract": BalanceQueryContract(
                account_scope="all", operation="breakdown", response_shape="surface_list"
            ).model_dump(mode="json"),
        },
        context={"profile": {"id": "u_1"}, "language": "en", "accounts": []},
        user_message="Check balance",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response is not None
    assert result.response.startswith("Your Balances")
    assert result.outbox[0]["body_blocks"] == [
        {"type": "heading", "text": "Your Balances"},
        {"type": "text", "text": "Access Bank (···0003): ₦30,000.00"},
        {"type": "text", "text": "GTBank (···0002): ₦30,000.00"},
        {"type": "key_value", "label": "Total", "value": "₦60,000.00"},
    ]


async def test_account_worker_returns_only_combined_total_for_three_selected_accounts() -> None:
    accounts = [
        SimpleNamespace(account_id="access", bank_name="Access Bank", account_number="1234560003"),
        SimpleNamespace(account_id="gtb", bank_name="GTBank", account_number="1234560002"),
        SimpleNamespace(account_id="first", bank_name="First Bank", account_number="1234560001"),
    ]
    worker = AccountWorker(
        account_repo=_AccountsRepo(accounts),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_MappedBalanceProvider(
            {"access": Decimal("10000"), "gtb": Decimal("30000"), "first": Decimal("20000")}
        ),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={
            "action": "check_balance",
            "skip_parse": True,
            "read_request": {"subject": "balance", "response_shape": "fact_value"},
            "balance_contract": {
                "account_scope": "named",
                "bank_names": ["Access Bank", "GTBank", "First Bank"],
                "operation": "total",
                "response_shape": "fact_value",
            },
        },
        context={"profile": {"id": "u_1"}, "language": "en", "accounts": accounts},
        user_message="Give me their combined balance",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response == "That gives you a total of **₦60,000.00**."
    assert result.outbox[0]["body_blocks"] == [{"type": "text", "text": "That gives you a total of **₦60,000.00**."}]


async def test_account_worker_compares_three_accounts_in_descending_balance_order() -> None:
    accounts = [
        SimpleNamespace(account_id="access", bank_name="Access Bank", account_number="1234560003"),
        SimpleNamespace(account_id="gtb", bank_name="GTBank", account_number="1234560002"),
        SimpleNamespace(account_id="first", bank_name="First Bank", account_number="1234560001"),
    ]
    worker = AccountWorker(
        account_repo=_AccountsRepo(accounts),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_MappedBalanceProvider(
            {"access": Decimal("10000"), "gtb": Decimal("30000"), "first": Decimal("20000")}
        ),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={
            "action": "check_balance",
            "skip_parse": True,
            "read_request": {"subject": "balance", "response_shape": "surface_list"},
            "balance_contract": {
                "account_scope": "named",
                "bank_names": ["Access Bank", "GTBank", "First Bank"],
                "operation": "compare",
                "response_shape": "surface_list",
            },
        },
        context={"profile": {"id": "u_1"}, "language": "en", "accounts": accounts},
        user_message="Compare the accounts",
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response is not None
    assert result.response.index("GTBank") < result.response.index("First Bank") < result.response.index("Access Bank")


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
