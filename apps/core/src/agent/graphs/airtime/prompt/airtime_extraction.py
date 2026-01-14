"""Airtime extraction prompt for LLM-based entity extraction."""

AIRTIME_EXTRACTION_PROMPT = (
    "Extract airtime purchase entities from user messages.\n\n"
    "**FIELDS:**\n"
    "- amount: Convert shortcuts (2k→2000.0, 5h→500.0, 1.5k→1500.0)\n"
    "- recipient_phone: Normalize to 11-digit format (08012345678)\n"
    "- network: MTN, Airtel, Glo, 9mobile (standardize case)\n"
    "- recipient_name: Name/alias if mentioned ('for mum')\n"
    "- is_self: true if 'my line', 'for me', 'myself' (don't mark phone missing)\n"
    "- narration, source_account_id: optional\n\n"
    "**CORRECTIONS:**\n"
    "When user corrects a value, output correction object:\n"
    "- correction: {field: 'amount', new_value: 5000}\n"
    "Examples:\n"
    "- 'I meant 5k' → correction: {field: 'amount', new_value: 5000}\n"
    "- 'make it Airtel' → correction: {field: 'network', new_value: 'Airtel'}\n\n"
    "**AMBIGUITIES:**\n"
    "Output ambiguities array when clarification needed:\n"
    "- AMOUNT_UNCLEAR: '5' could be ₦5 or ₦5,000\n"
    "- NETWORK_UNCLEAR: Can't determine network from phone\n"
    "- RECIPIENT_UNCLEAR: Can't determine who to send to\n\n"
    "**MISSING FIELDS:**\n"
    "List only: 'amount', 'recipientPhone', 'network'\n"
    "If is_self=true, don't mark phone/network missing.\n\n"
    "**EXAMPLES:**\n"
    '{"entities":{"amount":2000.0},"missingFields":["recipientPhone","network"],"reply":"₦2,000 airtime. Phone number and network?"}\n'
    '{"entities":{"amount":5000.0,"network":"MTN"},"missingFields":["recipientPhone"],"reply":"₦5,000 MTN. Which number?"}\n'
    '{"entities":{"amount":2000.0,"is_self":true},"missingFields":[],"reply":"₦2,000 to your line."}\n\n'
    "**CORRECTION EXAMPLES:**\n"
    'User: "I meant 5k"\n'
    '{"entities":{"amount":5000.0},"correction":{"field":"amount","new_value":5000},"missingFields":[],"reply":"Alright, updating to ₦5,000."}\n\n'
    "**AMBIGUITY EXAMPLES:**\n"
    'User: "buy 5 airtime"\n'
    '{"entities":{"amount":5},"ambiguities":["AMOUNT_UNCLEAR"],"missingFields":["recipientPhone","network"],"reply":"₦5 airtime? (Did you mean ₦5,000?) Phone and network?"}\n'
)
