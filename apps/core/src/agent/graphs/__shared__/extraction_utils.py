from typing import Any

from shared.utils.logging import get_logger

logger = get_logger(__name__)


def try_extract_numeric_index(user_message: str, domain: str) -> dict[str, Any] | None:
    """
    Attempts to extract a numeric index (1-9) from a user message.
    Used as a deterministic fallback for account selection.
    """
    clean_msg = user_message.strip()
    if clean_msg.isdigit() and len(clean_msg) == 1:
        index = int(clean_msg)
        logger.info(f"{domain}_extraction_numeric_fallback", index=index)
        return {
            "source_account_index": index,
            "source_account_id": None,  # Force re-selection by index
            "confirmation": {"confirmed": False},
        }
    return None
