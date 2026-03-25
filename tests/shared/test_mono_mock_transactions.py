import pytest

from shared.clients.providers.mono.client import MonoClient
from shared.clients.providers.mono.mock_data import reset_mock_transaction_state
from shared.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_mock_state() -> None:
    reset_mock_transaction_state()
    yield
    reset_mock_transaction_state()


@pytest.mark.asyncio
async def test_mono_client_mock_transactions_apply_date_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings, "app_env", "production")

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
    monkeypatch.setattr(settings, "app_env", "production")

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
    monkeypatch.setattr(settings, "app_env", "production")

    client = MonoClient()
    user_1_first = await client.get_transactions("acc_u1_1", limit=5, user_id="user_1")
    user_2_first = await client.get_transactions("acc_u2_1", limit=5, user_id="user_2")

    assert [txn.id for txn in user_1_first] == [txn.id for txn in user_2_first]


@pytest.mark.asyncio
async def test_mono_client_mock_transactions_use_account_slot_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mono_use_mock_override", True)
    monkeypatch.setattr(settings, "app_env", "production")

    client = MonoClient()
    first_account = await client.get_transactions("acc_1", limit=5, user_id="user_1", mock_account_slot=0)
    second_account = await client.get_transactions("acc_2", limit=5, user_id="user_1", mock_account_slot=1)

    assert first_account[0].id == "txn_001"
    assert second_account[0].id == "txn_b01"
