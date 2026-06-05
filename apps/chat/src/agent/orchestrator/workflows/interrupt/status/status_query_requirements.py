def _friendly_required_field(field: str) -> str:
    mapping = {
        "recipient_name": "recipient name",
        "recipient_account": "recipient account number",
        "recipient_bank_name": "recipient bank name",
        "recipient_phone": "phone line",
        "target_phone": "phone line",
        "phone": "phone line",
        "network": "mobile network",
        "data_plan_id": "data plan choice",
        "data_plan_preference": "budget or data size",
        "beneficiary_id": "beneficiary selection",
        "source_account_id": "source account selection",
        "account_id": "account selection",
        "account_selection": "account selection",
        "identifier": "record selection",
        "authorization": "authorization",
        "transaction_reference": "transaction reference",
        "reference": "reference",
        "date_range": "date range",
        "time_range": "date range",
        "schedule_id": "scheduled transaction selection",
        "schedule_selector": "scheduled transaction selection",
        "schedule_time": "schedule time",
        "amount": "amount",
        "pin": "PIN authorization",
        "confirmation_summary": "confirmation",
    }
    return mapping.get(field, field.replace("_", " "))


def _build_requirements_hint(required_fields: list[str], interrupt_kind: str) -> str:
    hints: list[str] = []
    if "beneficiary_id" in required_fields:
        hints.append("Pick a beneficiary option by tapping it or replying with the number.")
    if "data_plan_id" in required_fields:
        hints.append("Pick a data plan by replying with the option number.")
    if "data_plan_preference" in required_fields:
        hints.append("Reply with a budget or size, like 2k or 5GB.")
    if any(field in required_fields for field in ("recipient_phone", "target_phone", "phone")):
        hints.append("Reply with the phone number, or say my line if it is for you.")
    if "network" in required_fields:
        hints.append("Reply with the network, like MTN, Airtel, Glo, or 9mobile.")
    if "source_account_id" in required_fields:
        hints.append("Pick the source account by tapping it or replying with the number.")
    if any(field in required_fields for field in ("account_id", "account_selection", "identifier")):
        hints.append("Pick the account or record by tapping it or replying with the number.")
    if any(field in required_fields for field in ("transaction_reference", "reference")):
        hints.append("Reply with the transaction reference, receipt, or enough details to identify it.")
    if any(field in required_fields for field in ("date_range", "time_range")):
        hints.append("Reply with a date range, like today, last week, or March 1 to March 15.")
    if any(field in required_fields for field in ("schedule_id", "schedule_selector")):
        hints.append("Pick the scheduled transaction by tapping it or replying with the number.")
    if "schedule_time" in required_fields:
        hints.append("Reply with the date and time for the schedule.")
    if "authorization" in required_fields:
        hints.append("Complete authorization to continue.")
    if "amount" in required_fields:
        hints.append("Reply with the amount (for example 5000).")
    if interrupt_kind == "confirmation":
        hints.append("Reply yes to continue or no to cancel.")
    if interrupt_kind == "auth":
        hints.append("Complete PIN authorization to continue.")
    return " ".join(hints)


__all__ = ["_build_requirements_hint", "_friendly_required_field"]
