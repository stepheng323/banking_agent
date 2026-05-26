from shared.database.models import Transaction


def test_transaction_model_supports_mobile_biller_fields() -> None:
    columns = Transaction.__table__.c

    for name in (
        "target_phone_number",
        "mobile_network",
        "biller_code",
        "biller_item_code",
        "biller_item_name",
        "service_metadata",
    ):
        assert name in columns
        assert columns[name].nullable is True


def test_transaction_transfer_recipient_fields_are_nullable() -> None:
    columns = Transaction.__table__.c

    for name in (
        "recipient_account_number",
        "recipient_bank_code",
        "recipient_bank_name",
        "recipient_name",
    ):
        assert columns[name].nullable is True
