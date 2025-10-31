"""Prompts for the transfer agent."""

INTENT_PARSER_PROMPT = """You are an intent parser for a banking system.
 Extract transfer information from the user's message.

User message: "{user_message}"

Extract and return JSON with this structure:

{{
    "intent": "money_transfer",
    "recipient": {{
        "name": "extracted name or alias (e.g., 'mummy', 'John', 'church')",
        "account_number": "if explicitly mentioned",
        "bank_name": "if explicitly mentioned"
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
- "Send ₦5000 to mummy" → {{"amount": {{"value": 5000}}, "recipient": {{"name": "mummy"}}}}
- "Pay my tithe" → {{"recipient": {{"name": "tithe"}}, "amount": {{"needs_calculation": true, "calculation_expression": "10% of income"}}}}
- "Send 5% of my balance to brother" → {{"amount": {{"needs_calculation": true, "calculation_expression": "5% of balance"}}, "recipient": {{"name": "brother"}}, "dependencies": ["check_balance"]}}
- "Transfer ₦10k to 0123456789 GTBank" → {{"amount": {{"value": 10000}}, "recipient": {{"account_number": "0123456789", "bank_name": "GTBank"}}}}

Return ONLY valid JSON, no explanation or markdown."""

CLARIFICATION_PROMPTS = {
    "ambiguous_recipient": """Generate a clear question to disambiguate between multiple beneficiaries.

Options:
{options}

Generate a natural question listing the options with clear identifiers (numbers or bank details).
Example: "I found 2 beneficiaries named 'Mummy':
1) Mummy (GTBank ****1234)
2) Mummy Savings (Access ****5678)
Which one should I send to?"

Return only the question text.""",
    "recipient.account_number": """Generate a natural question asking for account number.
Recipient name: {recipient_name}
Example: "I don't have '{recipient_name}' saved. What's their account number?"
Return only the question text.""",
    "recipient.bank_code": """Generate a natural question asking for bank.
Example: "Which bank is this account with?"
You can add: "Reply with the bank name (e.g., 'GTBank', 'First Bank')"
Return only the question text.""",
    "amount.value": """Generate a natural question asking for transfer amount.
Recipient: {recipient_name}
Example: "How much would you like to send to {recipient_name}?"
Return only the question text.""",
    "source_account.account_id": """Generate a natural question asking which account to send from.
Accounts:
{accounts}
Example: "Which account should I send from?
1) Savings Account (Balance: ₦50,000)
2) Current Account (Balance: ₦120,000)"
Return only the question text.""",
}

__all__ = [
    "CLARIFICATION_PROMPTS",
    "INTENT_PARSER_PROMPT",
]
