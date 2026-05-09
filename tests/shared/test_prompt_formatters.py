from shared.formatters.prompts import format_single_transfer_recipient_prompt, sanitize_recipient_display_name


def test_sanitize_recipient_display_name_uses_guardrail_relationship_aliases() -> None:
    assert sanitize_recipient_display_name("my sister") == "your sister"
    assert sanitize_recipient_display_name("My mum") == "your mum"
    assert sanitize_recipient_display_name("sister") == "your sister"


def test_sanitize_recipient_display_name_uses_generic_label_for_unknown_first_person_phrase() -> None:
    assert sanitize_recipient_display_name("my landlord") == "the recipient"


def test_sanitize_recipient_display_name_keeps_normal_alias() -> None:
    assert sanitize_recipient_display_name("Tolu") == "Tolu"


def test_single_transfer_prompt_uses_second_person_relationship_label() -> None:
    assert (
        format_single_transfer_recipient_prompt(
            focused_name="my sister",
            focused_missing_fields=["recipient_account", "recipient_bank_name"],
            just_resolved_name=None,
            just_resolved_bank=None,
            found_names=[],
        )
        == "Please share the account number and bank for your sister."
    )


def test_single_transfer_batch_prompt_uses_still_need_possessive_after_found_recipient() -> None:
    assert (
        format_single_transfer_recipient_prompt(
            focused_name="Gaines",
            focused_missing_fields=["recipient_account", "recipient_bank_name"],
            just_resolved_name=None,
            just_resolved_bank=None,
            found_names=["Tolu Adebayo"],
        )
        == "I found Tolu Adebayo.\n\nI still need Gaines' account number and bank."
    )
