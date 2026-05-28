from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from apps.chat.src.agent.orchestrator.models.domain import SupportOutcome
from apps.chat.src.agent.workers.support.diagnostic_agent import (
    SupportDiagnosticAgent,
    build_support_diagnostic_context,
)
from apps.chat.src.agent.workers.support.diagnostic_routing import SupportDiagnosticRouter
from apps.chat.src.agent.workers.support.models import (
    ClassificationResult,
    PendingReferenceState,
    SupportContext,
    SupportDiagnosticAction,
    SupportDiagnosticDecision,
    SupportIntent,
    SupportReferenceCandidate,
    TransactionReference,
)
from apps.chat.src.agent.workers.support.worker import SupportWorker
from shared.config.settings import settings


class _StructuredLLM:
    def __init__(self, decision: Any) -> None:
        self.decision = decision
        self.messages: list[Any] = []
        self.schema: object | None = None

    def with_structured_output(self, schema: object) -> _StructuredLLM:
        self.schema = schema
        return self

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.messages = messages
        return self.decision


class _RedisStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        del ttl
        self.values[key] = value

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def hgetall(self, key: str):
        return dict(self.hashes.get(key, {}))


class _TxRepoStub:
    def __init__(self, transactions: dict[str, object]) -> None:
        self.transactions = transactions

    async def get_by_id(self, transaction_id: str):
        return self.transactions.get(transaction_id)

    async def get_by_idempotency_key(self, idempotency_key: str):
        del idempotency_key
        return None

    async def get_by_user(self, user_id: str, limit: int = 50):
        del user_id
        transactions = list(self.transactions.values())
        transactions.sort(key=lambda tx: getattr(tx, "created_at", datetime.min), reverse=True)
        return transactions[:limit]

    async def get_by_status(self, user_id: str, status: str):
        del user_id
        return [tx for tx in self.transactions.values() if getattr(tx, "status", None) == status]


class _ActionableRepoStub:
    async def get_by_channel_message_id_for_user(self, channel_message_id: str, user_id: str):
        del channel_message_id, user_id
        return None


def _tx(
    transaction_id: str,
    *,
    status: str = "failed",
    error_message: str = "Provider down",
    failure_category: str = "provider_unavailable",
):
    return SimpleNamespace(
        id=transaction_id,
        transaction_type="transfer",
        transaction_id=transaction_id,
        status=status,
        amount=10000,
        currency="NGN",
        recipient_name="Mercy Johnson",
        recipient_account_number="8162511023",
        recipient_bank_code="011",
        recipient_bank_name="Opay",
        source_bank_name="Zenith Bank",
        narration=None,
        error_message=error_message,
        failure_category=failure_category,
        provider_response={},
        provider_status=status,
        provider_error_code=None,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        completed_at=datetime.now(UTC).replace(tzinfo=None),
    )


def _worker(decision: Any, transactions: dict[str, object]) -> SupportWorker:
    return SupportWorker(
        llm=_StructuredLLM(decision),
        transaction_repo=_TxRepoStub(transactions),
        actionable_message_repo=_ActionableRepoStub(),
        redis_client=_RedisStub(),
    )


@pytest.mark.asyncio
async def test_support_diagnostic_agent_parses_structured_decision() -> None:
    decision = SupportDiagnosticDecision(
        intent=SupportIntent.FAILED_TRANSFER,
        next_action=SupportDiagnosticAction.EXPLAIN_TRANSACTION,
        transaction_ref=TransactionReference(use_recent=True),
        required_actions=["lookup_transaction"],
        reason="recent failed transaction",
        confidence=0.91,
    )
    llm = _StructuredLLM(decision)
    agent = SupportDiagnosticAgent(llm)

    result = await agent.decide({"message": "My last transaction failed"})

    assert result == decision
    assert llm.schema is SupportDiagnosticDecision
    assert "My last transaction failed" in llm.messages[1].content


@pytest.mark.asyncio
async def test_support_diagnostic_agent_rejects_invalid_action() -> None:
    agent = SupportDiagnosticAgent(
        _StructuredLLM(
            {
                "intent": "failed_transfer",
                "next_action": "send_money_now",
                "confidence": 0.99,
            }
        )
    )

    with pytest.raises(ValidationError):
        await agent.decide({"message": "bad route"})


def test_support_diagnostic_context_caps_recent_candidates() -> None:
    candidates = [
        SupportReferenceCandidate(
            transaction_id=f"tx-{idx}",
            ordinal=idx,
            task_type="transfer",
            amount=1000 * idx,
            final_status="failed",
        )
        for idx in range(1, 8)
    ]

    context = build_support_diagnostic_context(
        message="retry failed one",
        locale="en",
        support_context=SupportContext(
            last_ticket_id="SUP-1",
            pending_reference=PendingReferenceState(candidates=candidates, intent="failed_transfer"),
        ),
        intent=SupportIntent.RETRY_TRANSFER,
        classification=ClassificationResult(
            intent=SupportIntent.RETRY_TRANSFER,
            confidence=0.9,
            transaction_ref=TransactionReference(use_recent=True),
        ),
        transaction={"transaction_id": "tx-0", "provider_response": {"secret": "omit"}},
        recent_candidates=candidates,
        ticket_code="SUP-20260513-0001",
        latest_ticket_id="SUP-1",
        capability_summary={"retry_payout": False},
    )

    assert len(context["recent_candidates"]) == 5
    assert len(context["support_context"]["pending_reference"]["candidates"]) == 5
    assert context["recent_candidates"][0]["transaction_id"] == "tx-1"
    assert "provider_response" not in context["resolved_transaction"]
    assert context["ticket"]["explicit_code"] == "SUP-20260513-0001"


@pytest.mark.asyncio
async def test_support_worker_diagnostic_explains_failed_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_support_diagnostic_agent", True)
    decision = SupportDiagnosticDecision(
        intent=SupportIntent.FAILED_TRANSFER,
        next_action=SupportDiagnosticAction.EXPLAIN_TRANSACTION,
        transaction_ref=TransactionReference(use_recent=True),
        reason="explain failure",
        confidence=0.92,
    )
    worker = _worker(decision, {"tx-1": _tx("tx-1")})

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="My last transaction failed",
    )

    assert result.outcome == SupportOutcome.OK
    assert "Provider down" in (result.response or "")


@pytest.mark.asyncio
async def test_support_worker_diagnostic_retry_blocked_by_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_support_diagnostic_agent", True)
    decision = SupportDiagnosticDecision(
        intent=SupportIntent.RETRY_TRANSFER,
        next_action=SupportDiagnosticAction.PREPARE_RETRY_HANDOFF,
        transaction_ref=TransactionReference(use_recent=True),
        reason="prepare retry",
        confidence=0.94,
    )
    worker = _worker(decision, {"tx-1": _tx("tx-1")})

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="Retry my last transaction",
    )

    assert result.handoff is None
    assert "can't *retry the transfer*" in (result.response or "")


@pytest.mark.asyncio
async def test_support_worker_diagnostic_retry_allowed_returns_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_support_diagnostic_agent", True)
    monkeypatch.setattr(SupportDiagnosticRouter, "_policy_block_for_actions", staticmethod(lambda actions, locale: None))
    decision = SupportDiagnosticDecision(
        intent=SupportIntent.RETRY_TRANSFER,
        next_action=SupportDiagnosticAction.PREPARE_RETRY_HANDOFF,
        transaction_ref=TransactionReference(use_recent=True),
        reason="prepare retry",
        confidence=0.94,
    )
    worker = _worker(decision, {"tx-1": _tx("tx-1")})

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="Retry my last transaction",
    )

    assert result.outcome == SupportOutcome.OK
    assert result.handoff is not None
    assert result.handoff["type"] == "retry_transfer"
    assert result.handoff["requires_confirmation"] is True
    assert result.handoff["payload"]["recipient_name"] == "Mercy Johnson"


@pytest.mark.asyncio
async def test_support_worker_diagnostic_low_confidence_falls_back_to_micro_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "enable_support_diagnostic_agent", True)
    decision = SupportDiagnosticDecision(
        intent=SupportIntent.FAILED_TRANSFER,
        next_action=SupportDiagnosticAction.NOT_SUPPORTED,
        reason="not confident",
        confidence=0.2,
    )
    worker = _worker(decision, {"tx-1": _tx("tx-1")})

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="My last transaction failed",
    )

    assert result.outcome == SupportOutcome.OK
    assert "Provider down" in (result.response or "")
