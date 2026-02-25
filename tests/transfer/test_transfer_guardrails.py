"""Transfer guardrail coverage tests."""

from types import SimpleNamespace

from apps.core.src.agent.graphs.transfer.models.types import TransferContext, TransferPayload
from apps.core.src.agent.graphs.transfer.nodes.confirmation import _build_dynamic_risk_patch, build_confirmation
from apps.core.src.agent.graphs.transfer.nodes.resolver import resolve_beneficiary


class _MockBankingProvider:
    def __init__(self, resolved_name: str) -> None:
        self.resolved_name = resolved_name

    async def resolve_account(self, account_number: str, bank_code: str) -> dict:
        del account_number, bank_code
        return {"success": True, "account_name": self.resolved_name}


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

    result = await resolve_beneficiary(payload, ctx, banking_provider=_MockBankingProvider("John Doe"), bank_cache=None)

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
    assert "Mercy Johnson" in result.confirmation_summary


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
