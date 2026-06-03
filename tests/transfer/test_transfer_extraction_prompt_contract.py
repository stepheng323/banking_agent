"""Contract tests for transfer extraction prompt guidance."""

from banking.transfers.extraction.prompt import TRANSFER_EXTRACTION_PROMPT


def test_required_fields_slot_fill_rules_include_spaced_digits() -> None:
    """Prompt should instruct account parsing from spaced/hyphenated digits."""
    assert "strip non-digits; if result is exactly 10 digits, map to `recipient_account`" in TRANSFER_EXTRACTION_PROMPT


def test_required_fields_slot_fill_rules_include_account_plus_bank() -> None:
    """Prompt should support one-turn slot fill for account + bank replies."""
    assert "extract BOTH `recipient_account` and `bank_name` in the same turn" in TRANSFER_EXTRACTION_PROMPT
    assert '"816 251 1023 opay"' in TRANSFER_EXTRACTION_PROMPT
    assert '"9162512056, opay"' in TRANSFER_EXTRACTION_PROMPT
    assert '"9162512056 - opay"' in TRANSFER_EXTRACTION_PROMPT


def test_numeric_reply_not_mapped_to_recipient_name() -> None:
    """Prompt should prevent numeric-looking replies from being treated as recipient names."""
    assert "If reply is numeric-looking, do NOT put it in `recipient_name`." in TRANSFER_EXTRACTION_PROMPT


def test_recipient_split_rules_are_separate_from_funding_split() -> None:
    """Prompt should separate recipient-side allocation from source-account split semantics."""
    assert "recipient_allocations" in TRANSFER_EXTRACTION_PROMPT
    assert "split 20k between mum and gaines" in TRANSFER_EXTRACTION_PROMPT.lower()
    assert "send 20k 70/30 btw mum and gaines" in TRANSFER_EXTRACTION_PROMPT.lower()
    assert "Do NOT use `explicit_split` for recipient names" in TRANSFER_EXTRACTION_PROMPT


def test_prompt_includes_account_aware_percentage_and_transfer_all_examples() -> None:
    """Prompt should guide account-aware amount extraction from selected source bank."""
    normalized = TRANSFER_EXTRACTION_PROMPT.lower()
    assert "send half my zenith to mum" in normalized
    assert "send everything in my first bank to tolu" in normalized
