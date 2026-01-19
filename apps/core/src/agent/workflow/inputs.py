"""Input matching rules for Workflow Session Gate."""

import re
from typing import Pattern

# Compiled regex patterns
ACCOUNT_NUMBER_PATTERN: Pattern = re.compile(r"\b\d{10}\b")
AMOUNT_PATTERN: Pattern = re.compile(r"\b\d+(?:k|m|b)?\b", re.IGNORECASE)
YES_NO_PATTERN: Pattern = re.compile(r"\b(yes|no|yeah|nah|yep|nope)\b", re.IGNORECASE)
CANCEL_PATTERN: Pattern = re.compile(r"\b(cancel|stop|abort|quit|nevermind)\b", re.IGNORECASE)


def matches_expected_input(message: str, expected_inputs: list[str]) -> bool:
    """Check if message content matches any of the expected input types."""
    if not expected_inputs:
        return False
        
    normalized = message.lower().strip()
    
    for input_type in expected_inputs:
        if input_type == "recipient_account":
            if ACCOUNT_NUMBER_PATTERN.search(message):
                return True
                
        elif input_type == "amount":
            if AMOUNT_PATTERN.search(message):
                return True
                
        elif input_type == "confirmation":
            if YES_NO_PATTERN.search(message):
                return True
                
        elif input_type == "bank_name":
            # Heuristic: Logic would be more complex, but length check helps
            if len(normalized) > 2 and not normalized.isdigit():
                return True
                
        elif input_type == "narration":
            # Almost anything can be narration, but avoid command words
            if len(normalized) > 0 and not CANCEL_PATTERN.match(normalized):
                return True
                
        elif input_type == "pin":
            # PINs usually don't come via text in this architecture, but if they did:
            if re.search(r"\b\d{4,6}\b", message):
                return True
    
    return False


def is_cancellation(message: str) -> bool:
    """Check if message is a cancellation request."""
    return bool(CANCEL_PATTERN.search(message.lower().strip()))
