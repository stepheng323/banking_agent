"""Response templates for fast template-based response generation."""

from .intent import ResponseIntent


# Templates support {variable} placeholders from ResponseContext
# Use friendly, conversational language

TEMPLATES: dict[ResponseIntent, dict[str, str]] = {
    # Collection intents
    ResponseIntent.ASK_AMOUNT: {
        "en": "How much would you like to send?",
        "yo": "Elo ni o fe fi ranse?",
    },
    ResponseIntent.ASK_RECIPIENT: {
        "en": "Who would you like to send money to? You can provide their name, account number, or both.",
        "yo": "Tani o fe fi owo ranse si?",
    },
    ResponseIntent.ASK_BANK: {
        "en": "Which bank is that for?",
        "yo": "Banki wo ni?",
    },
    ResponseIntent.ASK_ACCOUNT_NUMBER: {
        "en": "What's the account number for {recipient_name}?",
        "yo": "Nomba account {recipient_name} ko?",
    },
    ResponseIntent.ASK_PHONE_NUMBER: {
        "en": "What phone number should I send to?",
        "yo": "Nomba fonu wo ni mo fe fi ranse si?",
    },
    ResponseIntent.ASK_NETWORK: {
        "en": "Which network is {phone_masked}?",
        "yo": "Network wo ni {phone_masked}?",
    },
    ResponseIntent.ASK_DATA_PLAN: {
        "en": "Which data plan would you like?",
        "yo": "Data plan wo ni o fe?",
    },
    
    # Clarification intents
    ResponseIntent.CLARIFY_BENEFICIARY: {
        "en": "I found multiple matches for '{recipient_name}'. Which one did you mean?\n{candidates_list}",
        "yo": "Mo ri opo fun '{recipient_name}'. Ewo ni o fe?\n{candidates_list}",
    },
    ResponseIntent.CLARIFY_ACCOUNT: {
        "en": "Which of your accounts would you like to send from?",
        "yo": "Iru account wo ni o fe fi ranse lati?",
    },
    ResponseIntent.CLARIFY_BANK: {
        "en": "I couldn't find a bank called '{bank_name}'. Please provide the correct bank name.",
        "yo": "Mi o ri bank ti a npe ni '{bank_name}'. Jowo fun mi ni oruko bank to dara.",
    },
    
    # Confirmation intents
    ResponseIntent.CONFIRM_TRANSFER: {
        "en": "Just to confirm: {formatted_amount} to {recipient_name} at {bank_name}?",
        "yo": "Lati se idaniloju: {formatted_amount} si {recipient_name} ni {bank_name}?",
    },
    ResponseIntent.CONFIRM_AIRTIME: {
        "en": "Just to confirm: {formatted_amount} airtime to {phone_masked} ({network})?",
        "yo": "Lati se idaniloju: {formatted_amount} airtime si {phone_masked} ({network})?",
    },
    ResponseIntent.CONFIRM_DATA: {
        "en": "Just to confirm: {data_plan} data for {phone_masked} ({network})?",
        "yo": "Lati se idaniloju: {data_plan} data fun {phone_masked} ({network})?",
    },
    
    # Success intents
    ResponseIntent.TRANSFER_SUCCESS: {
        "en": "✅ Done! {formatted_amount} has been sent to {recipient_name}.",
        "yo": "✅ O ti pari! {formatted_amount} ti ranse si {recipient_name}.",
    },
    ResponseIntent.AIRTIME_SUCCESS: {
        "en": "✅ Done! {formatted_amount} airtime sent to {phone_masked}.",
        "yo": "✅ O ti pari! {formatted_amount} airtime ti ranse si {phone_masked}.",
    },
    ResponseIntent.DATA_SUCCESS: {
        "en": "✅ Done! {data_plan} data activated for {phone_masked}.",
        "yo": "✅ O ti pari! {data_plan} data ti bere fun {phone_masked}.",
    },
    
    # Error intents
    ResponseIntent.INSUFFICIENT_BALANCE: {
        "en": "You don't have enough balance. Available: {formatted_amount}.",
        "yo": "O ko ni owo to. Owo ti o wa: {formatted_amount}.",
    },
    ResponseIntent.ACCOUNT_NOT_FOUND: {
        "en": "I couldn't find that account. Please check the account number and try again.",
        "yo": "Mi o ri account yen. Jowo wo nomba account ati gbiyanju lekan si.",
    },
    ResponseIntent.ACCOUNT_VALIDATION_FAILED: {
        "en": "I couldn't verify that account right now. Please try again.",
        "yo": "Mi o le se idaniloju account yen bayi. Jowo gbiyanju lekan si.",
    },
    ResponseIntent.BANK_NOT_FOUND: {
        "en": "I couldn't find a bank called '{bank_name}'. Please provide the correct bank name.",
        "yo": "Mi o ri bank ti a npe ni '{bank_name}'. Jowo fun mi ni oruko bank to dara.",
    },
    ResponseIntent.SESSION_EXPIRED: {
        "en": "Your session has expired. Please start again.",
        "yo": "Session re ti pari. Jowo bere lekan si.",
    },
    ResponseIntent.PIN_FAILED: {
        "en": "Incorrect PIN. Please try again.",
        "yo": "PIN ko tona. Jowo gbiyanju lekan si.",
    },
    ResponseIntent.MAX_ATTEMPTS_EXCEEDED: {
        "en": "Maximum attempts exceeded. Please start a new transaction.",
        "yo": "O ti gbiyanju pupoju. Jowo bere idunadura titun.",
    },
    ResponseIntent.TRANSFER_FAILED: {
        "en": "Transfer failed: {error_message}",
        "yo": "Gbigbe owo ko sise: {error_message}",
    },
    ResponseIntent.AIRTIME_FAILED: {
        "en": "Airtime purchase failed: {error_message}",
        "yo": "Rira airtime ko sise: {error_message}",
    },
    ResponseIntent.DATA_FAILED: {
        "en": "Data purchase failed: {error_message}",
        "yo": "Rira data ko sise: {error_message}",
    },
    ResponseIntent.INVALID_AMOUNT: {
        "en": "Invalid amount. {error_message}",
        "yo": "Iye ko tona. {error_message}",
    },
    ResponseIntent.INVALID_PHONE_NUMBER: {
        "en": "Invalid phone number. Please enter a valid Nigerian number (e.g., 08012345678).",
        "yo": "Nọmba fonu ko to. Jowo tẹ nọmba to tona (fun apẹẹrẹ, 08012345678).",
    },
    ResponseIntent.INVALID_NETWORK: {
        "en": "Invalid network. Please choose from: MTN, Airtel, Glo, or 9mobile.",
        "yo": "Network ko tona. Jowo yan lati: MTN, Airtel, Glo, tabi 9mobile.",
    },
    
    # Flow control intents
    ResponseIntent.CANCELLED: {
        "en": "Transaction cancelled.",
        "yo": "Idunadura ti fagile.",
    },
    ResponseIntent.MANDATE_REQUIRED: {
        "en": "⚠️ {error_message}",
        "yo": "⚠️ {error_message}",
    },
    ResponseIntent.ACCOUNT_SELECTION_REQUIRED: {
        "en": "Which account would you like to use?",
        "yo": "Iru account wo ni o fe lo?",
    },
    
    # Conversational intents
    ResponseIntent.GREETING: {
        "en": "Hi{user_greeting}! How can I help you today?",
        "yo": "Bawo{user_greeting}! Bawo ni mo se le ran e lowo loni?",
    },
    ResponseIntent.HELP: {
        "en": "I can help you with:\n• Sending money\n• Buying airtime\n• Buying data\n• Checking your balance\n\nWhat would you like to do?",
        "yo": "Mo le ran e lowo pelu:\n• Fifiranṣẹ owo\n• Rira airtime\n• Rira data\n• Wiwo balance\n\nKini o fe se?",
    },
    ResponseIntent.BENEFICIARY_SAVED: {
        "en": "✅ Saved {recipient_name} as a beneficiary.",
        "yo": "✅ A ti fi {recipient_name} pamo gege bi olugba.",
    },
    
    # Acknowledgment intents
    ResponseIntent.RECIPIENT_CHANGED: {
        "en": "Got it, updating the recipient to {recipient_name}.",
        "yo": "O ti gba, a n yi olugba pada si {recipient_name}.",
    },
    ResponseIntent.AMOUNT_CHANGED: {
        "en": "Got it, updating the amount to {formatted_amount}.",
        "yo": "O ti gba, a n yi iye pada si {formatted_amount}.",
    },
}


def get_template(intent: ResponseIntent, language: str = "en") -> str | None:
    """Get template for intent in specified language.
    
    Falls back to English if language not available.
    Returns None if intent has no template (requires LLM).
    """
    templates = TEMPLATES.get(intent)
    if not templates:
        return None
    
    return templates.get(language) or templates.get("en")
