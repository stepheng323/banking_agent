"""Tests for deterministic i18n locale and rendering behavior."""

from __future__ import annotations

import re

import pytest

from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.message_keys import ALL_MESSAGE_KEYS
from banking.presentation.i18n.models import (
    LanguageDetectionSignal,
    LocaleCode,
)
from banking.presentation.i18n.renderer import (
    _flatten_string_leaves,
    _read_catalog,
    render_message,
    render_text,
    validate_catalog_completeness,
)

_NON_EN_LOCALES = (LocaleCode.PCM, LocaleCode.YO, LocaleCode.HA, LocaleCode.IG)

_PRIORITY_DUPLICATE_GROUPS = {
    "locale",
    "common",
    "response",
    "orchestrator",
    "interrupt",
    "transaction_copy",
    "transfer",
    "airtime",
    "data",
    "support",
    "query",
    "account",
    "beneficiary",
    "schedule",
    "funding",
    "progress",
}

_NEUTRAL_DUPLICATE_VALUES = {
    "",
    "N/A",
    "????",
    "─────────────",
    "─────────────────",
    "; ",
    " - {message}",
    " ({context}",
}

_NEUTRAL_DUPLICATE_WORDS = {
    "account",
    "accounts",
    "airtime",
    "amount",
    "another",
    "bank",
    "banki",
    "beneficiaries",
    "beneficiary",
    "data",
    "name",
    "now",
    "number",
    "plan",
    "phone",
    "recipient",
    "receipt",
    "receipts",
    "recently",
    "request",
    "summary",
    "today",
    "top",
    "transaction",
    "transactions",
    "transfer",
    "transfers",
}

_STRUCTURAL_DUPLICATE_SUFFIXES = (
    "accounts_item",
    "async_status_message",
    "breakdown_heading",
    "candidate_item",
    "context_suffix",
    "divider",
    "funding_item",
    "heading_period_range",
    "heading_suffix_account",
    "heading_suffix_period",
    "last4_fallback",
    "location_line",
    "na",
    "natural_multi_item",
    "option_line",
    "plan_list.item",
    "plan_selection.option",
    "ranked_heading",
    "ranked_item",
    "recipient_line",
    "separator",
    "source_repair_bullet",
    "status_pending_generic",
    "suggested_item",
    "timeframe_range",
    "title",
    "transaction_item",
    "transaction_item_with_bank",
    "with_time",
)

_STRUCTURAL_DUPLICATE_KEYS = {
    "account.balance.header_multi",
    "account.balance.header_single",
    "account.default_updated",
    "account.linking.fallback_link",
    "account.list.commands_hint",
    "account.list.default_badge",
    "account.prompt.default_identifier",
    "account.prompt.unlink_identifier",
    "account.unlink.only_account",
    "airtime.execution.system_error",
    "airtime.extraction.unsupported_feature",
    "airtime.format.summary.network_line",
    "airtime.validation.join_many",
    "airtime.validation.join_two",
    "beneficiary.add.missing_account_or_bank",
    "beneficiary.prompt.which_contact",
    "beneficiary.suggestion.default_alias",
    "beneficiary.update.not_supported",
    "data.confirmation.buy_network_for_phone",
    "data.confirmation.buy_plan_for_phone",
    "data.error.system_processing",
    "data.execution.receipt_description_network",
    "data.execution.receipt_description_plan",
    "data.format.summary.network_amount",
    "data.format.validity_days",
    "funding.batch.task_covered",
    "funding.batch.task_short",
    "funding.format.insufficient.option_cancel",
    "funding.format.insufficient.option_send_instead",
    "funding.format.insufficient.options_header",
    "funding.format.plan.multi_source_header",
    "funding.format.plan.multi_source_item",
    "funding.planner.preferred_insufficient",
    "orchestrator.error.audio_unprocessable",
    "orchestrator.execution.batch_total_only",
    "orchestrator.execution.need_account_details_for",
    "orchestrator.execution.need_some_details",
    "orchestrator.execution.queued_next_notice",
    "orchestrator.execution.recipient_resolved",
    "orchestrator.execution.recipient_resolved_generic",
    "orchestrator.execution.recipient_resolved_with_bank",
    "orchestrator.execution.source_repair_discrepancy",
    "orchestrator.execution.source_repair_question",
    "orchestrator.execution.source_repair_use_bank",
    "orchestrator.execution.source_repair_use_bank_for_both",
    "progress.scope.transactions_with",
    "progress.scope.with_category",
    "progress.time.this_month",
    "query.affordability.can_afford",
    "query.affordability.cannot_afford",
    "query.analytics.adjective_biggest",
    "query.analytics.adjective_smallest",
    "query.analytics.breakdown_by",
    "query.analytics.target_account",
    "query.analytics.target_counterparty",
    "query.analytics.target_counterparty_credit",
    "query.analytics.target_counterparty_debit",
    "query.analytics.timeframe_default",
    "query.analytics.timeframe_on_date",
    "query.analytics.timeframe_yesterday",
    "query.analytics.title_smallest",
    "query.balance.single_account",
    "query.beneficiary.heading_most_frequent",
    "query.beneficiary.no_outgoing_transfers",
    "query.beneficiary.summary_header",
    "query.beneficiary.summary_line",
    "query.fetch.local.transfer_to",
    "query.fetch.local.wallet",
    "query.format.breakdown_item",
    "query.format.field_category",
    "query.format.field_counterparty",
    "query.format.field_date",
    "query.format.field_ref",
    "query.format.heading_spending_category",
    "query.format.narration.type_for_recipient",
    "query.format.no_results_time_suffix_period",
    "query.format.remaining_transactions",
    "query.format.total_line",
    "query.format.transactions_across_accounts",
    "query.format.transfer_reply_hint",
    "query.time_comparison.item_income",
    "query.time_comparison.item_spending",
    "response.templates.acknowledge_change",
    "response.templates.beneficiary_saved",
    "response.templates.cancellation_continue",
    "response.templates.invalid_amount",
    "response.templates.transfer_all_acknowledged",
    "schedule.confirmation.line",
    "schedule.row.with_id",
    "schedule.target.airtime_with_network",
    "schedule.target.data_with_network",
    "schedule.time.with_timezone",
    "support.receipt.ref",
    "transaction_copy.completion.header.airtime_plural",
    "transaction_copy.completion.header.data_plural",
    "transfer.confirmation.change.account_to",
    "transfer.confirmation.change.amount_to",
    "transfer.confirmation.change.bank_to",
    "transfer.confirmation.change.details_fallback",
    "transfer.confirmation.change.join_many",
    "transfer.confirmation.change.join_two",
    "transfer.confirmation.change.narration_to",
    "transfer.confirmation.change.recipient_to",
    "transfer.format.funding_plan.suggested_header",
    "transfer.format.funding_plan.total_line",
    "transfer.format.multi_source_receipt.from_line",
    "transfer.format.multi_source_receipt.funded_header",
    "transfer.format.multi_source_receipt.ref",
    "transfer.format.multi_source_receipt.success_header",
    "transfer.format.multi_source_summary.field_to",
    "transfer.format.multi_source_summary.funding_header",
    "transfer.format.summary.source_line",
    "transfer.format.summary.user_note",
    "transfer.resolve.my_bank_account",
    "transfer.resolve.my_bank_name",
    "transfer.resolve.need_bank_name_for_account",
}


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        del ex
        self._store[key] = value

    async def delete(self, key: str):
        self._store.pop(key, None)


def test_locale_normalize_aliases():
    assert LocaleManager.normalize("english") == LocaleCode.EN
    assert LocaleManager.normalize("pidgin") == LocaleCode.PCM
    assert LocaleManager.normalize("pingin") == LocaleCode.EN
    assert LocaleManager.normalize("yoruba") == LocaleCode.YO
    assert LocaleManager.normalize("hausa") == LocaleCode.HA
    assert LocaleManager.normalize("igbo") == LocaleCode.IG


def test_parse_locale_name():
    assert LocaleManager.parse_locale_name("english") == LocaleCode.EN
    assert LocaleManager.parse_locale_name("English") == LocaleCode.EN
    assert LocaleManager.parse_locale_name("pidgin") == LocaleCode.PCM
    assert LocaleManager.parse_locale_name("pingin") is None
    assert LocaleManager.parse_locale_name("pingin", allow_fuzzy=True) == LocaleCode.PCM
    assert LocaleManager.parse_locale_name("yoruba") == LocaleCode.YO
    assert LocaleManager.parse_locale_name("hausa") == LocaleCode.HA
    assert LocaleManager.parse_locale_name("igbo") == LocaleCode.IG
    assert LocaleManager.parse_locale_name("  Yoruba  ") == LocaleCode.YO
    assert LocaleManager.parse_locale_name(None) is None
    assert LocaleManager.parse_locale_name("french") is None


def test_render_message_and_bridge():
    text = render_message("common.safe_capability_fallback", "pcm")
    assert "I never fit do" in text

    en = render_message("common.safe_capability_fallback", "en")
    assert render_text(en, "pcm") == text


def test_pidgin_transfer_confirmation_copy_is_localized():
    assert render_message("transaction_copy.confirmation.transfer_variants.warm", "pcm") == "Confirm transfer"
    assert render_message("transaction_copy.confirmation.mixed", "pcm") == "Confirm all transactions"
    assert render_message("transaction_summary.batch.confirm_title", "pcm", {"count": 2}) == "*Confirm transfers (2)*"
    assert (
        render_message(
            "transfer.format.summary.title_variants.warm", "pcm", {"amount": "₦2,000", "recipient_name": "Tolu"}
        )
        == "*Confirm: ₦2,000 to Tolu*"
    )
    assert (
        render_message(
            "response.templates.clarify_beneficiary", "pcm", {"recipient_name": "Tolu", "candidates_list": "1. Tolu A"}
        )
        == "I see more than one match for 'Tolu'. Which one you mean?\n1. Tolu A"
    )


def test_pidgin_transaction_copy_avoids_awkward_hybrid_labels():
    rendered_strings = [
        render_message(
            "transfer.format.summary.title_variants.warm", "pcm", {"amount": "₦2,000", "recipient_name": "Tolu"}
        ),
        render_message("transfer.format.summary.user_note", "pcm", {"user_note": "For lunch"}),
        render_message("transfer.format.multi_source_summary.narration", "pcm", {"narration": "For lunch"}),
        render_message(
            "airtime.format.summary.title_variants.warm",
            "pcm",
            {"amount": "₦2,000", "recipient_display": "your own number"},
        ),
        render_message(
            "data.format.summary.title_variants.warm",
            "pcm",
            {"plan_name": "MTN 1.5 GB", "target_display": "your own number"},
        ),
        render_message("orchestrator.execution.source_account_info", "pcm", {"bank": "Access Bank", "last4": "0003"}),
        render_message("query.receipt.generating", "pcm"),
        render_message("query.format.pagination_showing", "pcm", {"showing": "1-2", "total": 2}),
    ]
    joined = "\n".join(rendered_strings)

    assert "E ready" not in joined
    assert "Comot from" not in joined
    assert "narration: Narration" not in joined
    assert "standard:" not in joined
    assert "warm:" not in joined
    assert "send it to you" not in joined
    assert "Showing 1-2 out of 2" not in joined
    assert "From account: Access Bank (···0003)" in joined
    assert "Showing 1-2/2" in joined
    assert "Reason: For lunch" in joined


@pytest.mark.asyncio
async def test_locale_hysteresis_auto_switch(monkeypatch):
    fake_redis = _FakeRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    phone = "2348000000000"

    first = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.EN, confidence=0.95, source="planner"),
    )
    assert first == LocaleCode.EN

    second = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.YO, confidence=0.95, source="planner"),
    )
    assert second == LocaleCode.EN

    third = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.YO, confidence=0.95, source="planner"),
    )
    assert third == LocaleCode.YO


@pytest.mark.asyncio
async def test_explicit_locale_switch_overrides(monkeypatch):
    fake_redis = _FakeRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    phone = "2348111111111"
    resolved = await LocaleManager.set_locale(phone, "pcm", source="user_command")
    assert resolved == LocaleCode.PCM

    effective = await LocaleManager.get_effective_locale(phone)
    assert effective == LocaleCode.PCM


@pytest.mark.asyncio
async def test_explicit_locale_lock_blocks_auto_detection_override(monkeypatch):
    fake_redis = _FakeRedis()

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls: fake_redis))

    phone = "2348222222222"
    resolved = await LocaleManager.set_locale(phone, "pcm", source="user_command")
    assert resolved == LocaleCode.PCM

    updated = await LocaleManager.update_locale(
        phone,
        LanguageDetectionSignal(locale=LocaleCode.EN, confidence=0.99, source="planner"),
    )

    assert updated == LocaleCode.PCM
    assert fake_redis._store[f"user:{phone}:language"] == "pcm"
    assert fake_redis._store[f"user:{phone}:language_explicit"] == "1"


def test_catalog_completeness():
    validate_catalog_completeness()


def _allowed_neutral_duplicate(message_key: str, value: str) -> bool:
    if message_key in _STRUCTURAL_DUPLICATE_KEYS:
        return True

    stripped = value.strip()
    if stripped in _NEUTRAL_DUPLICATE_VALUES:
        return True

    without_placeholders = re.sub(r"{[^{}]+}", " ", stripped)
    if not re.search(r"[A-Za-z]", without_placeholders):
        return True

    if message_key.endswith(_STRUCTURAL_DUPLICATE_SUFFIXES):
        return True

    words = re.findall(r"[A-Za-z]+", without_placeholders.lower())
    return bool(words) and all(word in _NEUTRAL_DUPLICATE_WORDS for word in words)


def test_priority_locale_catalogs_do_not_reuse_english_prose():
    en_messages = _flatten_string_leaves(_read_catalog(LocaleCode.EN))

    for locale in _NON_EN_LOCALES:
        locale_messages = _flatten_string_leaves(_read_catalog(locale))
        duplicates = [
            key
            for key, english_value in en_messages.items()
            if key.split(".", maxsplit=1)[0] in _PRIORITY_DUPLICATE_GROUPS
            and locale_messages.get(key) == english_value
            and not _allowed_neutral_duplicate(key, english_value)
        ]

        assert duplicates == []


@pytest.mark.parametrize(
    (
        "locale",
        "fallback",
        "processing_error",
        "confirmation",
        "completion",
        "amount_prompt",
        "source_prompt",
        "reversal",
        "status",
        "receipt_offer",
    ),
    [
        (
            "pcm",
            "Sorry, something no work.",
            "I dey get issue",
            "Confirm transfer",
            "Transfer Don Complete",
            "Send the amount",
            "source account",
            "I no fit reverse",
            "transfer to Tolu successful",
            "make I send receipt image",
        ),
        (
            "yo",
            "Ma binu",
            "iṣoro",
            "Jẹrisi Transfer",
            "Transfer ti pari",
            "Fesi pelu amount",
            "source account",
            "Ko le ṣe reversal",
            "Transfer ₦2000 si Tolu",
            "aworan receipt",
        ),
        (
            "ha",
            "Yi hakuri",
            "matsala",
            "Tabbatar da Transfer",
            "Transfer ya kammala",
            "Amsa da amount",
            "source account",
            "Ba za a iya reversal",
            "Transfer na ₦2000 zuwa Tolu",
            "hoton receipt",
        ),
        (
            "ig",
            "Ndo",
            "nsogbu",
            "Kwenye Transfer",
            "Transfer agwụla",
            "Zaa na amount",
            "source account",
            "Enweghị reversal",
            "Transfer ₦2000 nye Tolu",
            "foto receipt",
        ),
    ],
)
def test_high_traffic_locale_copy_is_not_english(
    locale: str,
    fallback: str,
    processing_error: str,
    confirmation: str,
    completion: str,
    amount_prompt: str,
    source_prompt: str,
    reversal: str,
    status: str,
    receipt_offer: str,
):
    assert fallback in render_message("response.fallback.generic", locale)
    assert processing_error in render_message("orchestrator.fallback.processing_error", locale)
    assert render_message("transaction_copy.confirmation.transfer", locale) == confirmation
    assert completion in render_message("transaction_copy.completion.header.transfer", locale)
    assert amount_prompt in render_message("interrupt.return_to_flow.input.amount", locale)
    assert source_prompt in render_message("interrupt.return_to_flow.input.source_account_id", locale)
    assert reversal in render_message("support.reversal.success_no_reversal", locale)
    assert status in render_message(
        "support.status.success_no_time",
        locale,
        {"amount": "2000", "recipient": "Tolu"},
    )
    assert receipt_offer in render_message("query.receipt.offer", locale)


def test_message_key_typing_in_sync():
    en_keys = set(_flatten_string_leaves(_read_catalog(LocaleCode.EN)).keys())
    assert set(ALL_MESSAGE_KEYS) == en_keys


@pytest.mark.parametrize(
    ("locale", "expected_snippet"),
    [
        ("pcm", "You wan save"),
        ("yo", "Ṣe o fẹ́"),
        ("ha", "Kana so"),
        ("ig", "Ị chọrọ"),
    ],
)
def test_beneficiary_suggestion_prompts_are_localized(locale: str, expected_snippet: str):
    text = render_message(
        "beneficiary.suggestion.ask_save_phone_default",
        locale,
        {
            "recipient_display": "Mum",
            "network": "MTN",
            "masked_phone": "…4567",
        },
    )

    assert expected_snippet in text
    assert "Mum" in text
    assert "MTN" in text
    assert "…4567" in text
    assert "Would you like" not in text
