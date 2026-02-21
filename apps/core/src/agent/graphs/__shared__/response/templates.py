"""Response templates for fast template-based response generation."""

from .intent import ResponseIntent

TEMPLATE_KEYS: dict[ResponseIntent, dict[str, str]] = {
    ResponseIntent.ASK_AMOUNT: {"default": "response.templates.ask_amount"},
    ResponseIntent.ASK_RECIPIENT: {
        "default": "response.templates.ask_recipient",
        "no_name": "response.templates.ask_recipient_no_name",
    },
    ResponseIntent.ASK_BANK: {"default": "response.templates.ask_bank"},
    ResponseIntent.ASK_ACCOUNT_NUMBER: {"default": "response.templates.ask_account_number"},
    ResponseIntent.ASK_PHONE_NUMBER: {"default": "response.templates.ask_phone_number"},
    ResponseIntent.ASK_NETWORK: {"default": "response.templates.ask_network"},
    ResponseIntent.ASK_DATA_PLAN: {"default": "response.templates.ask_data_plan"},
    ResponseIntent.CLARIFY_BENEFICIARY: {"default": "response.templates.clarify_beneficiary"},
    ResponseIntent.CLARIFY_ACCOUNT: {"default": "response.templates.clarify_account"},
    ResponseIntent.CLARIFY_BANK: {"default": "response.templates.clarify_bank"},
    ResponseIntent.CONFIRM_TRANSFER: {"default": "response.templates.confirm_transfer"},
    ResponseIntent.CONFIRM_AIRTIME: {"default": "response.templates.confirm_airtime"},
    ResponseIntent.CONFIRM_DATA: {"default": "response.templates.confirm_data"},
    ResponseIntent.TRANSFER_SUCCESS: {"default": "response.templates.transfer_success"},
    ResponseIntent.AIRTIME_SUCCESS: {"default": "response.templates.airtime_success"},
    ResponseIntent.DATA_SUCCESS: {"default": "response.templates.data_success"},
    ResponseIntent.INSUFFICIENT_BALANCE: {"default": "response.templates.insufficient_balance"},
    ResponseIntent.ACCOUNT_NOT_FOUND: {"default": "response.templates.account_not_found"},
    ResponseIntent.ACCOUNT_VALIDATION_FAILED: {"default": "response.templates.account_validation_failed"},
    ResponseIntent.BANK_NOT_FOUND: {"default": "response.templates.bank_not_found"},
    ResponseIntent.SESSION_EXPIRED: {"default": "response.templates.session_expired"},
    ResponseIntent.PIN_FAILED: {"default": "response.templates.pin_failed"},
    ResponseIntent.MAX_ATTEMPTS_EXCEEDED: {"default": "response.templates.max_attempts_exceeded"},
    ResponseIntent.TRANSFER_FAILED: {"default": "response.templates.transfer_failed"},
    ResponseIntent.AIRTIME_FAILED: {"default": "response.templates.airtime_failed"},
    ResponseIntent.DATA_FAILED: {"default": "response.templates.data_failed"},
    ResponseIntent.INVALID_AMOUNT: {"default": "response.templates.invalid_amount"},
    ResponseIntent.INVALID_PHONE_NUMBER: {"default": "response.templates.invalid_phone_number"},
    ResponseIntent.INVALID_NETWORK: {"default": "response.templates.invalid_network"},
    ResponseIntent.CANCELLED: {"default": "response.templates.cancelled"},
    ResponseIntent.MANDATE_REQUIRED: {"default": "response.templates.mandate_required"},
    ResponseIntent.ACCOUNT_SELECTION_REQUIRED: {"default": "response.templates.account_selection_required"},
    ResponseIntent.GREETING: {"default": "response.templates.greeting"},
    ResponseIntent.HELP: {"default": "response.templates.help"},
    ResponseIntent.BENEFICIARY_SAVED: {"default": "response.templates.beneficiary_saved"},
    ResponseIntent.RECIPIENT_CHANGED: {"default": "response.templates.recipient_changed"},
    ResponseIntent.AMOUNT_CHANGED: {"default": "response.templates.amount_changed"},
    ResponseIntent.ACKNOWLEDGE_CHANGE: {"default": "response.templates.acknowledge_change"},
    ResponseIntent.TRANSFER_ALL_ACKNOWLEDGED: {"default": "response.templates.transfer_all_acknowledged"},
    ResponseIntent.CANCELLATION_CONTINUE: {"default": "response.templates.cancellation_continue"},
}


def get_template(
    intent: ResponseIntent, language: str = "en", recipient_name: str | None = None
) -> str | None:
    """Get template key for intent.

    The `language` argument is retained for compatibility with existing callsites.

    Args:
        intent: The response intent
        language: Language code (unused)
        recipient_name: Optional recipient name for variant selection
    """
    del language
    templates = TEMPLATE_KEYS.get(intent)
    if not templates:
        return None

    if intent == ResponseIntent.ASK_RECIPIENT:
        if not recipient_name or recipient_name.lower() in ("recipient", ""):
            return templates.get("no_name")

    return templates.get("default")
