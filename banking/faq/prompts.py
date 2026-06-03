"""Prompts for the FAQ worker."""

from shared.config.settings import settings

# System prompt for answer synthesis (the only LLM-using node)
SYNTHESIS_SYSTEM_PROMPT = (
    f"You are a helpful customer service assistant for {settings.app_name}, a chat-based banking assistant built by "
    f"{settings.app_creator}.\n\n"
    """Your task is to answer the user's question using ONLY the provided FAQ content.

STRICT RULES:
1. Use ONLY information from the provided FAQ entries
2. Do NOT make up information or speculate
3. Do NOT mention "according to our FAQ" or similar meta-references
4. Keep responses concise and helpful
5. Use natural, conversational language
6. If the FAQ content doesn't fully answer the question, say so

FORMATTING:
- Use bullet points for lists
- Keep paragraphs short
- Use emojis sparingly for friendliness (1-2 max)
"""
)

SYNTHESIS_USER_PROMPT = """User's question: {question}

Relevant FAQ content:
{faq_content}

Provide a helpful, natural response based on the FAQ content above."""

# Forbidden scope patterns and responses
FORBIDDEN_PATTERNS = {
    "user_specific": {
        "patterns": [
            r"\bmy transfer\b",
            r"\bmy payment\b",
            r"\bmy transaction\b",
            r"\bmy money\b",
            r"\bmy account\b",
            r"\bmy balance\b",
            r"\bmy receipt\b",
        ],
        "reason": "user_specific_query",
    },
    "transaction_status": {
        "patterns": [
            r"\bwhy did (it|my|the) fail\b",
            r"\bwhere is (my|the) money\b",
            r"\bstatus of (my|the)\b",
            r"\bwhat happened to\b",
            r"\b(i was|i got|i have been|i'm|im) debited\b",
            r"\bdidn't receive\b",
            r"\bnot (received|delivered)\b",
        ],
        "reason": "transaction_status_query",
    },
    "dispute": {
        "patterns": [
            r"\brefund\b",
            r"\bdispute\b",
            r"\bchargeback\b",
            r"\breverse\b",
            r"\bwrong (amount|person|account)\b",
            r"\bsent to wrong\b",
        ],
        "reason": "dispute_query",
    },
}

# Category detection patterns
CATEGORY_PATTERNS = {
    "transfers": [
        r"\btransfer\b",
        r"\bsend\b",
        r"\bpay\b",
        r"\bpayment\b",
        r"\brecipient\b",
    ],
    "security": [
        r"\bpin\b",
        r"\bpassword\b",
        r"\bsecur\w*\b",
        r"\bfraud\b",
        r"\bprotect\b",
        r"\bsafe\b",
    ],
    "receipts": [
        r"\breceipt\b",
        r"\bproof\b",
        r"\bconfirm\w*\b",
    ],
    "data_purchase": [
        r"\bdata\b",
        r"\bbundle\b",
        r"\bairtime\b",
        r"\bmobile\b",
        r"\bmtn\b",
        r"\bglo\b",
        r"\bairtel\b",
    ],
    "account": [
        r"\baccount\b",
        r"\blink\b",
        r"\bbank\b",
        r"\bmandate\b",
    ],
}
