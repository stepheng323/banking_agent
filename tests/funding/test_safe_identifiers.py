from banking.transfers.funding.batch_models import BatchFundingAccount
from banking.transfers.nodes.funding import AccountAdapter
from shared.utils.user_error import looks_technical_error, safe_user_error_message


def test_funding_adapters_do_not_raise_for_non_uuid_demo_ids() -> None:
    account = {"id": "acc-access", "bank_name": "Access Bank", "account_number": "0003"}

    assert AccountAdapter(account).id == "acc-access"
    assert BatchFundingAccount(account).id == "acc-access"


def test_internal_uuid_errors_are_never_user_copy() -> None:
    internal = "badly formed hexadecimal UUID string"

    assert looks_technical_error(internal)
    message = safe_user_error_message(internal, task_type="transfer", locale="en")
    assert "hexadecimal" not in message.lower()
    assert "uuid" not in message.lower()
