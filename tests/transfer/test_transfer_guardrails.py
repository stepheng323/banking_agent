"""Transfer guardrail coverage tests."""

from types import SimpleNamespace

from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferPayload
from apps.core.src.agent.graphs.transfer.nodes.confirmation import _build_dynamic_risk_patch, build_confirmation
from apps.core.src.agent.graphs.transfer.nodes.resolver import resolve_beneficiary


class _MockBankingProvider:
    def __init__(self, resolved_name: str) -> None:
        self.resolved_name = resolved_name

    async def resolve_account(self, account_number: str, bank_code: str) -> SimpleNamespace:
        del account_number, bank_code
        return SimpleNamespace(
            success=True,
            account=SimpleNamespace(
                account_name=self.resolved_name,
                account_number="1234567890",
                bank_code="044",
            ),
        )


class _MockUnresolvedBankingProvider:
    async def resolve_account(self, account_number: str, bank_code: str) -> SimpleNamespace:
        del account_number, bank_code
        return SimpleNamespace(success=False, account=None)


class _MockTxRepo:
    def __init__(self, amounts: list[float]) -> None:
        self.amounts = amounts

    async def get_successful_transfers_since(self, user_id: str, since, limit: int = 500) -> list[SimpleNamespace]:
        del user_id, since, limit
        return [SimpleNamespace(amount=amt) for amt in self.amounts]


async def test_resolver_relational_alias_exempts_name_mismatch_warning() -> None:
    payload = TransferPayload(
        recipient_name="dad",
        recipient_account="1234567890",
        recipient_bank_code="044",
        recipient_bank_name="Access Bank",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = await resolve_beneficiary(
        payload,
        ctx,
        resolver_provider=_MockBankingProvider("John Doe"),
        bank_cache=None,
    )

    assert result.outcome.value == "ok"
    assert result.patch["recipient_resolved_name"] == "John Doe"
    assert result.patch["name_mismatch"] is False
    assert result.patch["name_mismatch_warning"] is None


async def test_confirmation_summary_includes_name_mismatch_warning() -> None:
    payload = TransferPayload(
        amount=5000,
        recipient_name="David",
        recipient_resolved_name="Mercy Johnson",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        name_mismatch_warning="You asked to send to David, but the account resolved as Mercy Johnson.",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = build_confirmation(payload, ctx)

    assert result.outcome.value == "needs_confirmation"
    assert result.confirmation_summary is not None
    assert "You asked to send to David" in result.confirmation_summary
    assert "₦5,000 → David (Mercy Johnson)" in result.confirmation_summary
    assert "Mercy Johnson" in result.confirmation_summary


async def test_confirmation_update_message_uses_specific_dynamic_ack() -> None:
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        previous_confirmation_snapshot={
            "amount": 10000,
            "recipient_name": "Mum",
            "recipient_bank": "Access Bank",
            "recipient_account": "1234567890",
        },
        transition_acknowledgment="Changing amount to 20k.",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = build_confirmation(payload, ctx)

    assert result.update_message == "Changing amount to 20k."


async def test_confirmation_update_message_falls_back_to_amount_template_when_ack_is_vague() -> None:
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        previous_confirmation_snapshot={
            "amount": 10000,
            "recipient_name": "Mum",
            "recipient_bank": "Access Bank",
            "recipient_account": "1234567890",
        },
        transition_acknowledgment="Updated.",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = build_confirmation(payload, ctx)

    assert result.update_message is not None
    assert "amount" in result.update_message.lower()
    assert "₦20,000" in result.update_message


async def test_confirmation_update_message_falls_back_to_recipient_template() -> None:
    payload = TransferPayload(
        amount=20000,
        recipient_name="Gaines",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        previous_confirmation_snapshot={
            "amount": 20000,
            "recipient_name": "Mum",
            "recipient_bank": "Access Bank",
            "recipient_account": "1234567890",
        },
        transition_acknowledgment="Got it.",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = build_confirmation(payload, ctx)

    assert result.update_message is not None
    assert "recipient" in result.update_message.lower()
    assert "Gaines" in result.update_message


async def test_confirmation_update_message_not_emitted_when_snapshot_unchanged() -> None:
    payload = TransferPayload(
        amount=20000,
        recipient_name="Mum",
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        previous_confirmation_snapshot={
            "amount": 20000,
            "recipient_name": "Mum",
            "recipient_bank": "Access Bank",
            "recipient_account": "1234567890",
        },
        transition_acknowledgment="Changing amount to 20k.",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = build_confirmation(payload, ctx)

    assert result.update_message is None


async def test_confirmation_update_message_ignores_cosmetic_recipient_and_bank_normalization() -> None:
    payload = TransferPayload(
        amount=8000,
        recipient_name="Mercy Johnson",
        recipient_account="0334555167",
        recipient_bank_name="GTBank",
        narration="Money for car repairs",
        previous_confirmation_snapshot={
            "amount": 8000,
            "recipient_name": "Gtb (Mercy Johnson)",
            "recipient_bank": "Gtb",
            "recipient_account": "0334555167",
            "narration": None,
        },
        transition_acknowledgment="Alright, changing recipient to Mercy Johnson, bank to GTBank, and narration to money for car repairs.",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = build_confirmation(payload, ctx)

    assert result.update_message is not None
    normalized = result.update_message.lower()
    assert "narration" in normalized or "note" in normalized
    assert "recipient" not in normalized
    assert "bank" not in normalized


async def test_saved_beneficiary_shortcut_is_used_when_recipient_not_changed() -> None:
    payload = TransferPayload(
        amount=6000,
        beneficiary_id="bene-1",
        recipient_name="Tolu Adebayo",
        recipient_account="2010000001",
        recipient_bank_name="Access Bank",
        recipient_bank_code="044",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-1",
                "alias": "Tolu",
                "account_name": "Tolu Adebayo",
                "account_number": "2010000001",
                "bank_name": "Access Bank",
                "bank_code": "044",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["resolved_from_saved_beneficiary"] is True
    assert result.patch["recipient_account"] == "2010000001"
    assert result.patch["recipient_name"] == "Tolu Adebayo"
    assert result.patch["recipient_resolved_name"] == "Tolu Adebayo"


async def test_saved_beneficiary_shortcut_is_dropped_when_recipient_changes() -> None:
    payload = TransferPayload(
        amount=6000,
        beneficiary_id="bene-1",
        recipient_name="Mercy Johnson",
        recipient_account="2010000001",
        recipient_bank_name="Access Bank",
        recipient_bank_code="044",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-1",
                "alias": "Tolu",
                "account_name": "Tolu Adebayo",
                "account_number": "2010000001",
                "bank_name": "Access Bank",
                "bank_code": "044",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert "recipient_account" in result.required_fields
    assert payload.beneficiary_id is None


async def test_name_only_single_beneficiary_match_autofills_recipient_details() -> None:
    payload = TransferPayload(
        amount=6000,
        recipient_name="Mum",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-2",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "GTBank",
                "bank_code": "058",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["resolved_from_saved_beneficiary"] is True
    assert result.patch["recipient_account"] == "2010000002"
    assert result.patch["recipient_bank_name"] == "GTBank"
    assert result.patch["recipient_name"] == "Mum"
    assert result.patch["recipient_resolved_name"] == "Mama Nkechi"


async def test_name_variant_only_single_beneficiary_match_autofills_recipient_details() -> None:
    payload = TransferPayload(
        amount=6000,
        recipient_name="mom",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-2",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "GTBank",
                "bank_code": "058",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["resolved_from_saved_beneficiary"] is True
    assert result.patch["recipient_account"] == "2010000002"
    assert result.patch["recipient_bank_name"] == "GTBank"
    assert result.patch["recipient_name"] == "mom"
    assert result.patch["recipient_resolved_name"] == "Mama Nkechi"


async def test_selected_beneficiary_prefers_alias_when_payload_name_missing() -> None:
    payload = TransferPayload(
        amount=6000,
        beneficiary_id="bene-1",
        recipient_account="2010000001",
        recipient_bank_name="Access Bank",
        recipient_bank_code="044",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-1",
                "alias": "Tolu",
                "account_name": "Tolu Adebayo",
                "account_number": "2010000001",
                "bank_name": "Access Bank",
                "bank_code": "044",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["recipient_name"] == "Tolu"
    assert result.patch["recipient_resolved_name"] == "Tolu Adebayo"


async def test_name_only_airtime_beneficiary_does_not_autofill_transfer() -> None:
    payload = TransferPayload(
        amount=6000,
        recipient_name="Mum",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-airtime-1",
                "beneficiary_type": "airtime",
                "alias": "Mum",
                "account_name": "Mum Airtime",
                "account_number": "08030000000",
                "bank_name": "MTN",
                "bank_code": "mtn",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account", "recipient_bank_name"]


async def test_transfer_match_prefers_transfer_beneficiary_when_alias_overlaps() -> None:
    payload = TransferPayload(
        amount=6000,
        recipient_name="Mum",
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        beneficiaries=[
            {
                "id": "bene-airtime-1",
                "beneficiary_type": "airtime",
                "alias": "Mum",
                "account_name": "Mum Airtime",
                "account_number": "08030000000",
                "bank_name": "MTN",
                "bank_code": "mtn",
            },
            {
                "id": "bene-transfer-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "GTBank",
                "bank_code": "058",
            },
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["beneficiary_id"] == "bene-transfer-1"
    assert result.patch["recipient_account"] == "2010000002"
    assert result.patch["recipient_bank_name"] == "GTBank"


async def test_verification_failure_requests_account_and_bank_not_recipient_name() -> None:
    payload = TransferPayload(
        recipient_account="1234567890",
        recipient_bank_name="Access Bank",
        recipient_bank_code="044",
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = await resolve_beneficiary(
        payload,
        ctx,
        resolver_provider=_MockUnresolvedBankingProvider(),
        bank_cache=None,
    )

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account", "recipient_bank_name"]
    assert "recipient_name" not in result.required_fields
    assert result.prompt is not None
    assert "account number and bank" in result.prompt.lower()


async def test_no_recipient_details_requests_account_and_bank_together() -> None:
    payload = TransferPayload()
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account", "recipient_bank_name"]
    assert result.prompt is not None
    assert "account number and bank" in result.prompt.lower()


async def test_only_bank_missing_requests_bank_only() -> None:
    payload = TransferPayload(recipient_account="1234567890")
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_bank_name"]
    assert result.prompt is not None
    assert "bank" in result.prompt.lower()


async def test_only_account_missing_requests_account_only() -> None:
    payload = TransferPayload(recipient_bank_name="Access Bank")
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account"]
    assert result.prompt is not None
    assert "account number" in result.prompt.lower()


async def test_pronoun_with_recent_beneficiary_context_single_match_autofills() -> None:
    payload = TransferPayload(amount=10000, recipient_name="her")
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        recent_beneficiary_context=True,
        beneficiaries=[
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "Opay",
                "bank_code": "100004",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["beneficiary_id"] == "bene-1"
    assert result.patch["recipient_bank_name"] == "Opay"


async def test_pronoun_with_recent_beneficiary_context_multiple_candidates_clarifies() -> None:
    payload = TransferPayload(amount=10000, recipient_name="her")
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        recent_beneficiary_context=True,
        beneficiaries=[
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "Opay",
                "bank_code": "100004",
            },
            {
                "id": "bene-2",
                "beneficiary_type": "transfer",
                "alias": "Dad",
                "account_name": "Papa Nkechi",
                "account_number": "2010000003",
                "bank_name": "GTBank",
                "bank_code": "058",
            },
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["beneficiary_id"]
    assert result.details.get("ambiguity") == "MULTIPLE_BENEFICIARIES"


async def test_pronoun_without_recent_beneficiary_context_does_not_autoresolve() -> None:
    payload = TransferPayload(amount=10000, recipient_name="her")
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        recent_beneficiary_context=False,
        beneficiaries=[
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "Opay",
                "bank_code": "100004",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account", "recipient_bank_name"]


async def test_pronoun_uses_previous_query_beneficiary_when_available() -> None:
    payload = TransferPayload(amount=10000, recipient_name="her")
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        recent_beneficiary_context=True,
        previous_beneficiary={
            "alias": "Mum",
            "account_name": "Mercy Johnson",
            "account_number": "2010000002",
            "bank_name": "Opay",
            "bank_code": "100004",
        },
        beneficiaries=[
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Dad",
                "account_name": "Papa Nkechi",
                "account_number": "2010000003",
                "bank_name": "GTBank",
                "bank_code": "058",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["recipient_bank_name"] == "Opay"
    assert result.patch["recipient_account"] == "2010000002"


async def test_reference_previous_does_not_guess_from_recent_beneficiary_list_without_explicit_previous() -> None:
    payload = TransferPayload(
        amount=10000,
        recipient_name="the previous one",
        recipient_reference={"selector": "previous"},
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        recent_beneficiary_context=True,
        beneficiaries=[
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "Opay",
                "bank_code": "100004",
            }
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account", "recipient_bank_name"]


async def test_reference_index_resolves_transfer_beneficiary() -> None:
    payload = TransferPayload(
        amount=10000,
        recipient_name="the second one",
        recipient_reference={"selector": "index", "index": 2},
    )
    ctx = TransferContext(
        phone_number="2348000000000",
        language="en",
        recent_beneficiary_context=True,
        beneficiaries=[
            {
                "id": "bene-1",
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mama Nkechi",
                "account_number": "2010000002",
                "bank_name": "Opay",
                "bank_code": "100004",
            },
            {
                "id": "bene-2",
                "beneficiary_type": "transfer",
                "alias": "Dad",
                "account_name": "Papa Nkechi",
                "account_number": "2010000003",
                "bank_name": "GTBank",
                "bank_code": "058",
            },
        ],
        accounts=[],
    )

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "ok"
    assert result.patch["beneficiary_id"] == "bene-2"
    assert result.patch["recipient_account"] == "2010000003"


async def test_invalid_recipient_placeholder_uses_generic_account_prompt() -> None:
    payload = TransferPayload(amount=8000, recipient_name="send's")
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])

    result = await resolve_beneficiary(payload, ctx, resolver_provider=None, bank_cache=None)

    assert result.outcome.value == "needs_input"
    assert result.required_fields == ["recipient_account", "recipient_bank_name"]
    assert result.prompt is not None
    assert "send's" not in result.prompt.lower()
    assert "her's" not in result.prompt.lower()


async def test_dynamic_risk_patch_flags_large_unsaved_transfer() -> None:
    payload = TransferPayload(
        amount=70000,
        recipient_name="Tolu",
        recipient_account="1234567890",
        recipient_bank_name="GTBank",
        beneficiary_id=None,
        resolved_from_saved_beneficiary=False,
        is_self=False,
    )
    ctx = TransferContext(phone_number="2348000000000", language="en", beneficiaries=[], accounts=[])
    worker_context = SimpleNamespace(
        user_id="user-1",
        transaction_repo=_MockTxRepo([2000, 3000, 4500]),
    )

    patch = await _build_dynamic_risk_patch(payload, ctx, worker_context)
    confirmed = build_confirmation(payload.model_copy(update=patch), ctx)

    assert patch["dynamic_risk_threshold"] >= 50000
    assert patch["is_high_risk_transfer"] is True
    assert isinstance(patch["high_risk_warning"], str) and patch["high_risk_warning"]
    assert confirmed.confirmation_summary is not None
    assert "high-risk transfer" in confirmed.confirmation_summary.lower()
