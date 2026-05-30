from banking.presentation.formatters.transaction_slot_prompts import format_transaction_slot_prompt


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

    assert prompt == "Got the MTN data. Which MTN line should I buy it for?"


def test_data_network_and_preference_prompt_preserves_worker_copy() -> None:
    fallback = "Sure. Which Airtel line should I buy for, and what budget or data size should I use?"
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={"network": "AIRTEL"},
        missing_fields=["target_phone", "data_plan_preference"],
        fallback_prompt=fallback,
        locale="en",
    )

    assert prompt == fallback


def test_data_selected_plan_phone_prompt_preserves_worker_copy() -> None:
    fallback = "I found AIRTEL 9GB data bundle for ₦4,000, valid 30 days. Which Airtel line should I buy it for?"
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={
            "network": "AIRTEL",
            "amount": 4000,
            "plan_code": "AD400",
            "plan_name": "AIRTEL 9GB data bundle",
        },
        missing_fields=["target_phone"],
        fallback_prompt=fallback,
        locale="en",
    )

    assert prompt == fallback


def test_airtime_known_amount_and_network_asks_for_matching_line_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="airtime",
        payload={"amount": 1000, "network": "MTN"},
        missing_fields=["recipient_phone"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Got ₦1,000.00 MTN airtime. Which MTN line should I buy it for?"


def test_data_known_phone_asks_for_network_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={"target_phone": "08162511023"},
        missing_fields=["network"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "I have 08162511023. Which network is it on?"


def test_airtime_self_line_asks_for_amount_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="airtime",
        payload={"recipient_phone": "08162511023", "network": "MTN", "is_self": True},
        missing_fields=["amount"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Sure. I'll use your MTN line. How much airtime should I buy?"


def test_airtime_network_known_asks_for_line_and_amount_naturally() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="airtime",
        payload={"network": "AIRTEL"},
        missing_fields=["recipient_phone", "amount"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Sure. Which Airtel line should I buy airtime for, and how much?"


def test_unknown_context_falls_back_to_existing_prompt() -> None:
    prompt = format_transaction_slot_prompt(
        task_type="data",
        payload={},
        missing_fields=["target_phone"],
        fallback_prompt="Fallback prompt",
        locale="en",
    )

    assert prompt == "Fallback prompt"
