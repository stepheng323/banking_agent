"""Contract tests for transfer extraction prompt guidance."""

from apps.core.src.agent.graphs.transfer.prompt.transfer_extraction import TRANSFER_EXTRACTION_PROMPT


def test_required_fields_slot_fill_rules_include_spaced_digits() -> None:
    """Prompt should instruct account parsing from spaced/hyphenated digits."""
    assert "strip non-digits; if result is exactly 10 digits, map to `recipient_account`" in TRANSFER_EXTRACTION_PROMPT


def test_required_fields_slot_fill_rules_include_account_plus_bank() -> None:
    """Prompt should support one-turn slot fill for account + bank replies."""
    assert "extract BOTH `recipient_account` and `bank_name` in the same turn" in TRANSFER_EXTRACTION_PROMPT
    assert '"816 251 1023 opay"' in TRANSFER_EXTRACTION_PROMPT


def test_numeric_reply_not_mapped_to_recipient_name() -> None:
    """Prompt should prevent numeric-looking replies from being treated as recipient names."""
    assert "If reply is numeric-looking, do NOT put it in `recipient_name`." in TRANSFER_EXTRACTION_PROMPT

