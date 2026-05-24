from shared.formatters.prompts import format_transaction_slot_prompt


def test_transfer_known_amount_asks_for_recipient_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="transfer",
        payload={"amount": 5000},
        missing_fields=["recipient_account", "recipient_bank_name"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Got ₦5,000.00. Who should I send it to?"


def test_transfer_known_recipient_asks_for_amount_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="transfer",
        payload={"recipient_name": "Tolu"},
        missing_fields=["amount"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "I found Tolu. How much should I send?"


def test_transfer_known_amount_and_recipient_asks_for_account_and_bank_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="transfer",
        payload={"amount": 5000, "recipient_name": "Tolu"},
        missing_fields=["recipient_account", "recipient_bank_name"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Got ₦5,000.00 for Tolu. Please share the account number and bank."


def test_transfer_known_account_asks_for_bank_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="transfer",
        payload={"recipient_account": "2010000001"},
        missing_fields=["recipient_bank_name"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "I have account 2010000001. Which bank is it?"


def test_data_known_network_asks_for_phone_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={"network": "MTN"},
        missing_fields=["target_phone"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Got the MTN data. Which line should I buy it for?"


def test_data_known_phone_asks_for_network_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={"target_phone": "08162511023"},
        missing_fields=["network"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "I have 08162511023. Which network is it on?"


def test_unknown_context_falls_back_to_existing_prompt() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={},
        missing_fields=["target_phone"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Fallback prompt"
