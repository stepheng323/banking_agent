import pytest

from shared.clients.providers.mono.client import MonoClient
from shared.clients.providers.mono.mock_data import reset_mock_transaction_state, update_mock_debit
from shared.clients.providers.mono.models import MonoApiError
from shared.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_mock_state() -> None:
    reset_mock_transaction_state()
    yield
    reset_mock_transaction_state()


class _CreateMandateClient(MonoClient):
    def __init__(self) -> None:
        self.use_mock = False
        self.api_key = "test-key"
        self.requests: list[dict] = []

    async def _request(self, method: str, path: str, body: dict | None = None, **kwargs) -> dict:
        del kwargs
        self.requests.append({"method": method, "path": path, "body": body or {}})
        return {
            "id": "mandate-1",
            "status": "pending",
            "mandate_type": body["mandate_type"] if body else "emandate",
            "debit_type": "variable",
            "amount": 1000000,
            "account_number": "0123456789",
            "bank_code": "058",
            "reference": "mandate-ref-1",
            "start_date": "2026-06-01",
            "end_date": "2027-06-01",
        }


@pytest.mark.asyncio
async def test_mono_client_create_mandate_defaults_to_documented_emandate() -> None:
    client = _CreateMandateClient()

    mandate = await client.create_mandate(
        customer_id="customer-1",
        account_number="0123456789",
        bank_code="058",
        amount=1000000,
        reference="mandate-ref-1",
        start_date="2026-06-01",
        end_date="2027-06-01",
    )

    assert client.requests[0]["path"] == "/v3/payments/mandates"
    assert client.requests[0]["body"]["mandate_type"] == "emandate"
    assert mandate.mandate_type == "emandate"


@pytest.mark.asyncio
async def test_mono_client_mock_transactions_apply_date_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    all_transactions = await client.get_transactions("acc_1", limit=50, user_id="user_1", mock_account_slot=0)
    target_day = all_transactions[0].date[:10]
    expected_ids = [txn.id for txn in all_transactions if txn.date[:10] == target_day]

    filtered = await client.get_transactions(
        "acc_1",
        start=target_day,
        end=target_day,
        limit=50,
        user_id="user_1",
        mock_account_slot=0,
    )

    assert [txn.id for txn in filtered] == expected_ids


@pytest.mark.asyncio
async def test_mono_client_mock_transactions_page_matches_unpaginated_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    all_transactions = await client.get_transactions("acc_1", limit=50, user_id="user_1", mock_account_slot=0)

    page_1, has_more_1, next_page_1 = await client.get_transactions_page(
        "acc_1",
        limit=5,
        page=1,
        user_id="user_1",
        mock_account_slot=0,
    )
    page_2, has_more_2, next_page_2 = await client.get_transactions_page(
        "acc_1",
        limit=5,
        page=2,
        user_id="user_1",
        mock_account_slot=0,
    )

    assert [txn.id for txn in page_1] == [txn.id for txn in all_transactions[:5]]
    assert [txn.id for txn in page_2] == [txn.id for txn in all_transactions[5:10]]
    assert has_more_1 is True
    assert next_page_1 == 2
    assert len({txn.id for txn in page_1}.intersection({txn.id for txn in page_2})) == 0
    assert has_more_2 is True
    assert next_page_2 == 3


@pytest.mark.asyncio
async def test_mono_client_mock_transactions_are_isolated_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    user_1_first = await client.get_transactions("acc_u1_1", limit=5, user_id="user_1")
    user_2_first = await client.get_transactions("acc_u2_1", limit=5, user_id="user_2")

    assert [txn.id for txn in user_1_first] == [txn.id for txn in user_2_first]


@pytest.mark.asyncio
async def test_mono_client_mock_transactions_use_account_slot_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    first_account = await client.get_transactions("acc_1", limit=5, user_id="user_1", mock_account_slot=0)
    second_account = await client.get_transactions("acc_2", limit=5, user_id="user_1", mock_account_slot=1)

    assert first_account[0].id == "txn_001"
    assert second_account[0].id == "txn_b01"


@pytest.mark.asyncio
async def test_mono_client_mock_debit_defaults_to_immediate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    initiated = await client.initiate_debit(
        mandate_id="mandate_123",
        amount=1_000_000,
        reference="transfer-ref-123",
        narration="Allowance",
        beneficiary_account="8162511023",
        beneficiary_bank_code="100004",
    )

    assert initiated["id"].startswith("mock_debit_")
    assert initiated["mandate"] == "mandate_123"
    assert initiated["status"] == "successful"
    assert initiated["reference"] == "transfer-ref-123"
    assert initiated["amount"] == 1_000_000
    assert initiated["narration"] == "Allowance"
    assert initiated["debit_type"] == "direct-to-beneficiary"
    assert initiated["response_code"] == "00"
    assert initiated["beneficiary"] == {
        "account_number": "8162511023",
        "bank_code": "100004",
    }

    status = await client.get_debit_status(initiated["id"])

    assert status["id"] == initiated["id"]
    assert status["status"] == "successful"
    assert status["reference"] == initiated["reference"]
    assert status["amount"] == initiated["amount"]
    assert status["response_code"] == "00"
    assert status["beneficiary"] == initiated["beneficiary"]


@pytest.mark.asyncio
async def test_mono_client_mock_debit_pending_lifecycle_can_still_be_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    initiated = await client.initiate_debit(
        mandate_id="mandate_123",
        amount=1_000_000,
        reference="transfer-ref-pending",
        narration="Allowance",
    )
    update_mock_debit(initiated["id"], status="pending", response_code=None)

    first_status = await client.get_debit_status(initiated["id"])
    second_status = await client.get_debit_status(initiated["id"])

    assert first_status["status"] == "processing"
    assert "response_code" not in first_status or first_status["response_code"] is None
    assert second_status["status"] == "successful"
    assert second_status["response_code"] == "00"


@pytest.mark.asyncio
async def test_mono_client_mock_get_debit_status_raises_for_unknown_debit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()

    with pytest.raises(MonoApiError) as exc_info:
        await client.get_debit_status("missing_mock_debit")

    assert exc_info.value.is_not_found is True


@pytest.mark.asyncio
async def test_mono_client_mock_failed_debit_uses_non_zero_response_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings.runtime, "app_env", "production")

    client = MonoClient()
    initiated = await client.initiate_debit(
        mandate_id="mandate_123",
        amount=500_000,
        reference="transfer-ref-failed",
        narration="Transport",
    )
    update_mock_debit(initiated["id"], status="failed")

    failed_status = await client.get_debit_status(initiated["id"])

    assert failed_status["status"] == "failed"
    assert failed_status["response_code"] == "51"
    assert failed_status["message"] == "Debit failed"
