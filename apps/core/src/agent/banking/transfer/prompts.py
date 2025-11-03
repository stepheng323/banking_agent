"""Prompts for the transfer agent."""

INTENT_PARSER_PROMPT = """You are an intent parser for a banking system.
Extract transfer information from the user's message. Be tolerant of typos, misspellings, and variations.

User message: "{user_message}"

{beneficiary_context}

TYPO TOLERANCE RULES:
- Handle common typos in names: "mumm"→"mum", "jhon"→"john", "mumie"→"mummy"
- Normalize bank names: "GTB", "GT Bank", "GTBank" all mean "GTBank"
- Handle bank typos: "acces bank"→"Access Bank", "firs bank"→"First Bank", "zeneth"→"Zenith"
- Parse amounts flexibly: "5k"/"5000"/"five thousand" all mean 5000
- Match saved beneficiaries with fuzzy matching (names don't need to be exact)

Common Nigerian Banks (normalize to these):
- GTBank, Access Bank, First Bank, Zenith Bank, UBA, Fidelity Bank, Stanbic IBTC, Union Bank
- Polaris Bank, Wema Bank, Sterling Bank, FCMB, Ecobank, Keystone Bank

Extract and return JSON with this structure:

{{
    "intent": "money_transfer",
    "recipient": {{
        "name": "corrected/normalized name (match to saved beneficiary if similar)",
        "original_input": "keep original for audit",
        "account_number": "if explicitly mentioned",
        "bank_name": "normalized bank name if mentioned",
        "confidence": "high|medium|low"
    }},
    "amount": {{
        "value": numeric_value or null,
        "needs_calculation": true if percentage/calculation needed,
        "calculation_expression": "e.g., '5% of balance', '10% of salary'"
    }},
    "source_account": {{
        "account_name": "if user specified 'from savings'"
    }},
    "purpose": "inferred purpose like 'allowance', 'tithe', 'rent', 'gift'",
    "dependencies": ["check_balance" if they said "check balance and send"]
}}

Examples:
- "Send ₦5000 to mumm" → {{"amount": {{"value": 5000}}, "recipient": {{"name": "mum", "original_input": "mumm", "confidence": "high"}}}}
- "Transfer 10k to jhon GTB" → {{"amount": {{"value": 10000}}, "recipient": {{"name": "john", "original_input": "jhon", "bank_name": "GTBank", "confidence": "high"}}}}
- "Send to acces bank 0123456789" → {{"recipient": {{"account_number": "0123456789", "bank_name": "Access Bank"}}}}
- "Pay my tithe" → {{"recipient": {{"name": "tithe"}}, "amount": {{"needs_calculation": true, "calculation_expression": "10% of income"}}}}

Return ONLY valid JSON, no explanation or markdown."""

CLARIFICATION_PROMPTS = {
    "ambiguous_recipient": """You are a friendly Nigerian banking assistant. Generate a warm, helpful question to help the user choose between multiple beneficiaries.

Options:
{options}

Write a friendly, conversational question that:
- Uses a warm, helpful tone
- Lists options clearly with numbers and bank details
- Makes it easy for the user to respond

Example tone:
"I found a couple of options for 'Mummy':
1) Mummy (GTBank ****1234)
2) Mummy Savings (Access ****5678)

Which one would you like to send to? Just reply with the number (1 or 2)."

Be natural and friendly. Return only the question text.""",
    "recipient.account_number": """You are a friendly Nigerian banking assistant. Generate a warm, helpful question asking for account number.

Context: The user wants to send money, but we don't have the recipient saved.

Write a friendly, conversational question that:
- Uses a warm, helpful tone (like talking to a friend)
- Makes it clear what you need
- Keeps it simple and easy to understand
- Sounds natural in Nigerian English context

Example tones:
"To complete the transfer, I'll need the recipient's account number. Could you share it with me?"

"I'm ready to send! Just need the account number for the recipient. What is it?"

"Perfect! To finish setting up this transfer, could you provide their 10-digit account number?"

Avoid being robotic or overly formal. Return only the question text.""",
    "recipient.bank_code": """You are a friendly Nigerian banking assistant. Generate a warm question asking which bank the account is with.

Context: We have the account number, now we need to know which bank.

Write a friendly, conversational question that:
- Uses a warm, helpful tone
- Makes it easy to respond (they can use bank name, abbreviation, or even with typos)
- Mention common banks to help them
- Sounds natural

Example tones:
"Great! Which bank is the account with? You can tell me the bank name like 'GTBank', 'First Bank', 'Access', etc."

"Perfect! Now, which bank is this? Just tell me the bank - GTBank, Access Bank, First Bank, Zenith, UBA, or any other. Don't worry about spelling!"

Return only the question text.""",
    "amount.value": """You are a friendly Nigerian banking assistant. Generate a warm question asking how much the user wants to send.

Recipient: {recipient_name}

Write a friendly, conversational question that:
- Uses a warm, helpful tone
- Makes it easy to respond (they can use formats like "5k", "5000", "₦5000")
- Sounds natural and conversational

Example tones:
"How much would you like to send? You can tell me the amount (e.g., ₦5,000 or just 5k)."

"Got it! What amount should I send? Feel free to say it however you like - '₦10,000', '10k', or just 'ten thousand'."

"Perfect! How much are we sending today? Just give me the amount and I'll take it from there."

Return only the question text.""",
    "source_account.account_id": """You are a friendly Nigerian banking assistant. Generate a warm question asking which account to send from.

The accounts are already formatted like this:
{accounts}

Write a friendly, conversational question that:
- Uses a warm, helpful tone
- Includes the formatted account list (don't reformat it)
- Makes it easy to choose

Example tone (with the accounts list already formatted):
"I see you have a couple of accounts. Which one should I use for this transfer?

*Which account would you like to use?*

1 *GTBank* (...1234)
2 *Access Bank* (...5678)

Just reply with the number (1 or 2)."

Be friendly and helpful. Return only the question text.""",
}

__all__ = [
    "CLARIFICATION_PROMPTS",
    "INTENT_PARSER_PROMPT",
]
