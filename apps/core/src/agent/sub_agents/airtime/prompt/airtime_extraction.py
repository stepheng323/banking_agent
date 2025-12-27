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
    "**CORRECTIONS (when pendingTransaction exists):**\n"
    "User is correcting - output the FULL corrected value:\n"
    "- 'last number is 4 not 3' + pendingPhone='08162511023' → '08162511024'\n"
    "- 'last digit is 4' + pendingPhone='08162511023' → '08162511024'\n"
    "- 'ends in 24' + pendingPhone='08162511023' → '08162511024'\n"
    "- 'I meant 5k' + pendingAmount=600 → 5000.0\n"
    "- 'make it Airtel' + pendingNetwork='MTN' → 'Airtel'\n\n"
    "**MISSING FIELDS:**\n"
    "List only: 'amount', 'recipientPhone', 'network'\n"
    "If is_self=true, don't mark phone/network missing.\n\n"
    "**SMART CONTEXT:**\n"
    "- beneficiaries: match names to saved contacts\n"
    "- recentPurchases: 'same as before' uses recentPurchases[0]\n"
    "- previousResponse: maintain tone consistency\n\n"
    "**EXAMPLES:**\n"
    '{"entities":{"amount":2000.0},"missingFields":["recipientPhone","network"],"reply":"₦2,000 airtime. Phone number and network?"}\n'
    '{"entities":{"amount":5000.0,"network":"MTN"},"missingFields":["recipientPhone"],"reply":"₦5,000 MTN. Which number?"}\n'
    '{"entities":{"recipient_phone":"08012345678","network":"MTN"},"missingFields":["amount"],"reply":"08012345678 (MTN). How much?"}\n'
    '{"entities":{"amount":2000.0,"recipient_phone":"08051234567","network":"Glo"},"missingFields":[],"reply":"₦2,000 Glo for 08051234567."}\n'
    '{"entities":{"amount":2000.0,"is_self":true},"missingFields":[],"reply":"₦2,000 to your line."}\n'
    '{"entities":{"amount":2000.0,"recipient_name":"mum"},"missingFields":["recipientPhone","network"],"reply":"₦2,000 for mum. Phone and network?"}\n\n'
    "**CORRECTION EXAMPLES (acknowledge change naturally):**\n"
    'User: "I meant 5k"\n'
    '{"entities":{"amount":5000.0},"missingFields":[],"reply":"Alright, updating to ₦5,000."}\n\n'
    'User: "Send to mum instead"\n'
    '{"entities":{"recipient_name":"mum"},"missingFields":["recipientPhone","network"],"reply":"Got it, sending to mum. Phone and network?"}\n'
)
