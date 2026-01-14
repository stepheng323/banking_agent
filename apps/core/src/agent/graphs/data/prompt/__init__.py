"""Data purchase extraction prompt for LLM-based entity extraction."""

DATA_EXTRACTION_PROMPT = (
    "Extract data purchase entities from user messages.\n\n"
    "**FIELDS:**\n"
    "- recipient_phone: Normalize to 11-digit format (08012345678)\n"
    "- network: MTN, AIRTEL, GLO, 9MOBILE (standardize case)\n"
    "- budget: Amount user wants to spend (2k→2000, 5h→500)\n"
    "- size_preference: '1GB', '2GB', '5GB', 'weekly', 'monthly', 'daily'\n"
    "- is_self: true if 'my line', 'for me', 'myself'\n"
    "- recipient_name: Name/alias if mentioned ('for mum')\n\n"
    "**CORRECTIONS:**\n"
    "When user corrects a value, output correction object:\n"
    "- correction: {field: 'budget', new_value: 5000}\n\n"
    "**AMBIGUITIES:**\n"
    "Output ambiguities array when clarification needed:\n"
    "- BUDGET_UNCLEAR: '5' could be ₦5 or ₦5,000\n"
    "- NETWORK_UNCLEAR: Can't determine network from phone\n"
    "- SIZE_UNCLEAR: Can't determine preferred data size\n\n"
    "**MISSING FIELDS:**\n"
    "List only: 'recipientPhone', 'network'\n"
    "If is_self=true, don't mark phone missing.\n\n"
    "**EXAMPLES:**\n"
    'User: "buy data"\n'
    '{"entities":{},"missingFields":["recipientPhone","network"],"reply":"Data purchase. Phone number and network?"}\n\n'
    'User: "buy 2k MTN data"\n'
    '{"entities":{"budget":2000,"network":"MTN"},"missingFields":["recipientPhone"],"reply":"₦2,000 MTN data. Which number?"}\n\n'
    'User: "buy 1GB data for my line"\n'
    '{"entities":{"size_preference":"1GB","is_self":true},"missingFields":[],"reply":"1GB data for your line."}\n\n'
    'User: "get 5GB for 08012345678"\n'
    '{"entities":{"size_preference":"5GB","recipient_phone":"08012345678"},"missingFields":["network"],"reply":"5GB for 08012345678. Which network?"}\n\n'
    'User: "buy mum 2GB airtel data"\n'
    '{"entities":{"size_preference":"2GB","network":"AIRTEL","recipient_name":"mum"},"missingFields":["recipientPhone"],"reply":"2GB Airtel for mum. Phone number?"}\n\n'
    "**CORRECTION EXAMPLES:**\n"
    'User: "I meant 5k"\n'
    '{"entities":{"budget":5000},"correction":{"field":"budget","new_value":5000},"missingFields":[],"reply":"Alright, ₦5,000 budget."}\n\n'
    'User: "make it MTN"\n'
    '{"entities":{"network":"MTN"},"correction":{"field":"network","new_value":"MTN"},"missingFields":[],"reply":"Got it, MTN."}\n'
)
