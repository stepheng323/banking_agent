from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from apps.chat.src.agent.graphs.support.handlers.failure import handle_failure_reason
from apps.chat.src.agent.graphs.support.worker import SupportWorker
from apps.chat.src.agent.orchestrator.models.domain import SupportOutcome
from shared.services.async_completion import record_group_leg_and_maybe_build_summary


class _SupportLLMStub:
    def __init__(self, content: str = "{}") -> None:
        self.content = content

    async def ainvoke(self, prompt: str):
        del prompt
        return SimpleNamespace(content=self.content)


class _RedisStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str):
        del ttl
        self.values[key] = value

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def hset(self, key: str, field: str, value: str):
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key: str, ttl: int):
        del key, ttl

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def hgetall(self, key: str):
        return dict(self.hashes.get(key, {}))


class _TxRepoStub:
    def __init__(self, transactions: dict[str, object]) -> None:
        self.transactions = transactions

    async def get_by_id(self, transaction_id: str):
        return self.transactions.get(transaction_id)

    async def get_by_idempotency_key(self, idempotency_key: str):
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
    amount: float,
    recipient_name: str,
    bank_name: str,
    account_number: str,
    status: str = "successful",
    error_message: str | None = None,
    failure_category: str | None = None,
    created_at: datetime | None = None,
):
    timestamp = created_at or datetime.now(UTC).replace(tzinfo=None)
    return SimpleNamespace(
        id=transaction_id,
        transaction_type="transfer",
        transaction_id=transaction_id,
        status=status,
        amount=amount,
        currency="NGN",
        recipient_name=recipient_name,
        recipient_account_number=account_number,
        recipient_bank_code="011",
        recipient_bank_name=bank_name,
        source_bank_name="Zenith Bank",
        narration=None,
        error_message=error_message,
        failure_category=failure_category,
        provider_response={},
        provider_status=status,
        provider_error_code="00" if status == "successful" else None,
        created_at=timestamp,
        completed_at=timestamp,
    )


def _worker(redis_client: _RedisStub, transactions: dict[str, object], ticket_service: object | None = None) -> SupportWorker:
    return SupportWorker(
        llm=_SupportLLMStub(),
        transaction_repo=_TxRepoStub(transactions),
        actionable_message_repo=_ActionableRepoStub(),
        redis_client=redis_client,
        ticket_service=ticket_service,
    )


class _TicketServiceStub:
    def __init__(self, ticket: object | None = None) -> None:
        self.ticket = ticket

    async def get_ticket(self, ticket_code: str):
        if self.ticket and getattr(self.ticket, "ticket_code", None) == ticket_code:
            return self.ticket
        return None

    async def get_latest_ticket(self, user_id: str):
        del user_id
        return self.ticket

    async def get_user_open_tickets(self, user_id: str):
        del user_id
        return [self.ticket] if self.ticket else []


@pytest.mark.asyncio
async def test_support_worker_enqueues_single_transfer_receipt_for_successful_transaction() -> None:
    worker = _worker(
        _RedisStub(),
        {
            "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
        },
    )

    result = await worker.run(
        payload={
            "intent": "receipt_request",
            "transaction": {
                "id": "tx-1",
                "transaction_type": "transfer",
                "transaction_id": "tx-1",
                "status": "successful",
                "amount": 10000,
                "recipient_name": "Mercy Johnson",
                "recipient_account_number": "8162511023",
                "recipient_bank_name": "Opay",
                "source_bank_name": "Zenith Bank",
                "narration": "Allowance",
            },
        },
        context={
            "phone_number": "2348162511023",
            "channel": "telegram",
            "channel_identity": "927331985",
            "language": "en",
        },
        user_message="Get me the receipt",
    )

    assert result.outcome == SupportOutcome.OK
    assert result.response is not None and "image shortly" in result.response
    assert len(result.receipt_jobs) == 1
    assert result.receipt_jobs[0]["transaction_reference"] == "tx-1"


@pytest.mark.asyncio
async def test_support_worker_handles_last_transaction_failed_when_llm_returns_alias() -> None:
    worker = SupportWorker(
        llm=_SupportLLMStub(
            '{"intent":"transfer_failure_reason","confidence":0.92,'
            '"transaction_ref":{"amount":null,"recipient_name":null,"date_hint":null}}'
        ),
        transaction_repo=_TxRepoStub(
            {
                "tx-1": _tx(
                    "tx-1",
                    amount=10000,
                    recipient_name="Mercy Johnson",
                    bank_name="Opay",
                    account_number="8162511023",
                    status="failed",
                    error_message="Provider down",
                    failure_category="provider_unavailable",
                )
            }
        ),
        actionable_message_repo=_ActionableRepoStub(),
        redis_client=_RedisStub(),
    )

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="My last transaction failed",
    )

    assert result.outcome == SupportOutcome.OK
    assert "Provider down" in (result.response or "")
    assert "retry now" in (result.response or "").lower()


@pytest.mark.asyncio
async def test_support_worker_last_transaction_failed_checks_latest_transaction_status() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    worker = SupportWorker(
        llm=_SupportLLMStub(),
        transaction_repo=_TxRepoStub(
            {
                "tx-processing": _tx(
                    "tx-processing",
                    amount=6000,
                    recipient_name="Tolu Adebayo",
                    bank_name="Opay",
                    account_number="8162511023",
                    status="processing",
                    created_at=now,
                ),
                "tx-failed": _tx(
                    "tx-failed",
                    amount=10000,
                    recipient_name="Mercy Johnson",
                    bank_name="Opay",
                    account_number="8162511023",
                    status="failed",
                    error_message="Provider down",
                    failure_category="provider_unavailable",
                    created_at=now - timedelta(minutes=5),
                ),
            }
        ),
        actionable_message_repo=_ActionableRepoStub(),
        redis_client=_RedisStub(),
    )

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="My last transaction failed",
    )

    assert result.outcome == SupportOutcome.OK
    assert "currently show it as processing, not failed" in (result.response or "")
    assert "Tolu Adebayo" in (result.response or "")
    assert "Provider down" not in (result.response or "")


@pytest.mark.asyncio
async def test_support_worker_show_details_uses_last_resolved_transaction() -> None:
    redis = _RedisStub()
    worker = _worker(
        redis,
        {
            "tx-success": _tx(
                "tx-success",
                amount=10000,
                recipient_name="Tolu Adebayo",
                bank_name="GTBank",
                account_number="8162511023",
                status="successful",
            ),
        },
    )

    first = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="My last transaction failed",
    )
    followup = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="show the details",
    )

    assert first.outcome == SupportOutcome.OK
    assert "actually successful" in (first.response or "")
    assert followup.outcome == SupportOutcome.OK
    assert "₦10,000" in (followup.response or "")
    assert "Tolu Adebayo" in (followup.response or "")
    assert "not sure" not in (followup.response or "").lower()


@pytest.mark.asyncio
async def test_support_worker_reference_followup_uses_last_transaction_after_wrong_debit_prompt() -> None:
    redis = _RedisStub()
    worker = _worker(
        redis,
        {
            "tx-success": _tx(
                "tx-success",
                amount=10000,
                recipient_name="Tolu Adebayo",
                bank_name="GTBank",
                account_number="8162511023",
                status="successful",
            ),
        },
    )

    first = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="I was debited but they didn't receive it",
    )
    followup = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="my last transaction",
    )

    assert first.outcome == SupportOutcome.NEEDS_INPUT
    assert "Which transaction" in (first.response or "")
    assert followup.outcome == SupportOutcome.OK
    assert "This transfer was successful" in (followup.response or "")
    assert "not sure" not in (followup.response or "").lower()


@pytest.mark.asyncio
async def test_support_worker_failed_transaction_phrase_prefers_failed_status() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    worker = SupportWorker(
        llm=_SupportLLMStub(),
        transaction_repo=_TxRepoStub(
            {
                "tx-processing": _tx(
                    "tx-processing",
                    amount=6000,
                    recipient_name="Tolu Adebayo",
                    bank_name="Opay",
                    account_number="8162511023",
                    status="processing",
                    created_at=now,
                ),
                "tx-failed": _tx(
                    "tx-failed",
                    amount=10000,
                    recipient_name="Mercy Johnson",
                    bank_name="Opay",
                    account_number="8162511023",
                    status="failed",
                    error_message="Provider down",
                    failure_category="provider_unavailable",
                    created_at=now - timedelta(minutes=5),
                ),
            }
        ),
        actionable_message_repo=_ActionableRepoStub(),
        redis_client=_RedisStub(),
    )

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="Why did my failed transaction fail?",
    )

    assert result.outcome == SupportOutcome.OK
    assert "Provider down" in (result.response or "")
    assert "Tolu Adebayo" not in (result.response or "")


@pytest.mark.asyncio
async def test_failure_handler_treats_provider_error_status_as_failed() -> None:
    response = await handle_failure_reason(
        {
            "id": "tx-1",
            "transaction_id": "tx-1",
            "transaction_type": "transfer",
            "status": "error",
            "amount": 10000,
            "recipient_name": "Mercy Johnson",
            "error_message": "Provider timed out",
            "provider_response": {},
        },
        locale="en",
    )

    assert "Provider timed out" in response.message
    assert "Unable to determine failure reason" not in response.message
    assert response.offer_retry is True


@pytest.mark.asyncio
async def test_failure_handler_processing_status_explains_current_state() -> None:
    response = await handle_failure_reason(
        {
            "id": "tx-1",
            "transaction_id": "tx-1",
            "transaction_type": "transfer",
            "status": "queued",
            "amount": 10000,
            "recipient_name": "Mercy Johnson",
            "provider_response": {},
        },
        locale="en",
    )

    assert "currently show it as processing, not failed" in response.message
    assert "Known reason: not available" in response.message
    assert "Unable to determine failure reason" not in response.message
    assert response.offer_retry is False


@pytest.mark.asyncio
async def test_support_worker_blocks_retry_when_policy_disables_retry_payout() -> None:
    worker = _worker(
        _RedisStub(),
        {
            "tx-1": _tx(
                "tx-1",
                amount=10000,
                recipient_name="Mercy Johnson",
                bank_name="Opay",
                account_number="8162511023",
                status="failed",
                error_message="Provider down",
                failure_category="provider_unavailable",
            )
        },
    )

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="Retry my last transaction",
    )

    assert result.handoff is None
    assert "can't *retry the transfer*" in (result.response or "")


@pytest.mark.asyncio
async def test_support_worker_returns_retry_handoff_when_policy_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.graphs.support.capabilities.check_unsupported_actions",
        lambda domain, requested_actions: [],
    )
    worker = _worker(
        _RedisStub(),
        {
            "tx-1": _tx(
                "tx-1",
                amount=10000,
                recipient_name="Mercy Johnson",
                bank_name="Opay",
                account_number="8162511023",
                status="failed",
                error_message="Provider down",
                failure_category="provider_unavailable",
            )
        },
    )

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="Retry my last transaction",
    )

    assert result.outcome == SupportOutcome.OK
    assert result.handoff is not None
    assert result.handoff["type"] == "retry_transfer"
    assert result.handoff["requires_confirmation"] is True
    assert result.handoff["payload"]["amount"] == 10000
    assert result.handoff["payload"]["recipient_name"] == "Mercy Johnson"


@pytest.mark.asyncio
async def test_support_worker_does_not_retry_replay_modifier_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.graphs.support.capabilities.check_unsupported_actions",
        lambda domain, requested_actions: [],
    )
    redis = _RedisStub()
    redis.values["support_context:user-1"] = (
        '{"last_transaction_ref":"tx-1","last_issue_intent":"failed_transfer","last_support_step":"resolved"}'
    )
    worker = _worker(
        redis,
        {
            "tx-1": _tx(
                "tx-1",
                amount=10000,
                recipient_name="Mercy Johnson",
                bank_name="Opay",
                account_number="8162511023",
                status="failed",
                error_message="Provider down",
                failure_category="provider_unavailable",
            )
        },
    )

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="Resend from gtb",
    )

    assert result.handoff is None
    assert result.outcome == SupportOutcome.OK


@pytest.mark.asyncio
async def test_support_worker_handles_ticket_status_with_latest_ticket() -> None:
    ticket = SimpleNamespace(
        ticket_code="SUP-20260513-0001",
        status="open",
        created_at=datetime(2026, 5, 13, 10, 30),
        resolved_at=None,
    )
    worker = _worker(_RedisStub(), {}, ticket_service=_TicketServiceStub(ticket))

    result = await worker.run(
        payload={},
        context={"user_id": "user-1", "phone_number": "2348162511023", "language": "en"},
        user_message="What is the status of my ticket?",
    )

    assert result.outcome == SupportOutcome.OK
    assert "SUP-20260513-0001" in (result.response or "")
    assert "open" in (result.response or "").lower()


def _group_message(tx_id: str, index: int) -> dict[str, object]:
    return {
        "transaction_id": tx_id,
        "async_group": {
            "async_group_id": "group-receipt",
            "async_group_size": 2,
            "async_group_kind": "multi_transfer",
            "async_group_index": index,
        },
        "channel": "telegram",
        "channel_identity": "927331985",
        "phone_number": "2348162511023",
    }


def _group_message_with_size(tx_id: str, index: int, size: int) -> dict[str, object]:
    return {
        "transaction_id": tx_id,
        "async_group": {
            "async_group_id": f"group-receipt-{size}",
            "async_group_size": size,
            "async_group_kind": "multi_transfer",
            "async_group_index": index,
        },
        "channel": "telegram",
        "channel_identity": "927331985",
        "phone_number": "2348162511023",
    }


def _leg_payload(*, amount: int, recipient_name: str, resolved_name: str, account: str, bank: str) -> dict[str, object]:
    return {
        "amount": amount,
        "recipient_name": recipient_name,
        "recipient_resolved_name": resolved_name,
        "recipient_account": account,
        "recipient_bank_name": bank,
        "final_status": "success",
    }


def _failed_leg_payload(
    *,
    amount: int,
    recipient_name: str,
    resolved_name: str,
    account: str,
    bank: str,
    error_message: str,
    failure_category: str,
) -> dict[str, object]:
    payload = _leg_payload(
        amount=amount,
        recipient_name=recipient_name,
        resolved_name=resolved_name,
        account=account,
        bank=bank,
    )
    payload["final_status"] = "failed"
    payload["error_message"] = error_message
    payload["failure_category"] = failure_category
    return payload


@pytest.mark.asyncio
async def test_support_worker_resolves_batch_receipt_followup_by_ordinal() -> None:
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )

    worker = _worker(
        redis_client,
        {
            "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
            "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
        },
    )

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="Get me the receipt for the second transaction",
    )

    assert result.outcome == SupportOutcome.OK
    assert "receipt" in (result.response or "").lower()
    assert len(result.receipt_jobs) == 1
    receipt_job = result.receipt_jobs[0]
    assert receipt_job["transaction_reference"] == "tx-2"
    assert receipt_job["transfer_data"]["recipient"]["name"] == "Tolu Adedayo"
    assert receipt_job["transfer_data"]["recipient"]["account_number"] == "0760505261"


@pytest.mark.asyncio
async def test_support_worker_keeps_batch_clarification_alive_for_acknowledgement_reply() -> None:
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )

    worker = _worker(redis_client, {})

    first = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="Get me the receipt for the transaction",
    )
    second = await worker.run(
        payload={},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="Ok",
    )

    assert first.outcome == SupportOutcome.NEEDS_INPUT
    assert "Are you referring to:" in first.response
    assert "1️⃣" in first.response
    assert "2️⃣" in first.response
    assert second.outcome == SupportOutcome.NEEDS_INPUT
    assert second.response == "Reply with 1 or 2."


@pytest.mark.asyncio
async def test_support_worker_enqueues_both_receipts_for_two_leg_batch() -> None:
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )

    worker = _worker(
        redis_client,
        {
            "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
            "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
        },
    )

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="Give the receipt for both",
    )

    assert result.outcome == SupportOutcome.OK
    assert [job["transaction_reference"] for job in result.receipt_jobs] == ["tx-1", "tx-2"]


@pytest.mark.asyncio
async def test_support_worker_both_on_three_leg_batch_repompts() -> None:
    redis_client = _RedisStub()
    for index, tx_id, amount, recipient, resolved, account, bank in (
        (1, "tx-1", 10000, "Mum", "Mercy Johnson", "8162511023", "Opay"),
        (2, "tx-2", 5000, "Tolu", "Tolu Adedayo", "0760505261", "First Bank"),
        (3, "tx-3", 7000, "Dad", "Dad Ade", "0011223344", "UBA"),
    ):
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message=_group_message_with_size(tx_id, index, 3),
            task_type="transfer",
            payload=_leg_payload(
                amount=amount,
                recipient_name=recipient,
                resolved_name=resolved,
                account=account,
                bank=bank,
            ),
            locale="en",
        )

    worker = _worker(redis_client, {})

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="both",
    )

    assert result.outcome == SupportOutcome.NEEDS_INPUT
    assert "Are you referring to:" in (result.response or "")
    assert "3️⃣" in (result.response or "")


@pytest.mark.asyncio
async def test_support_worker_all_except_last_selects_remaining_legs() -> None:
    redis_client = _RedisStub()
    txs = {}
    for index, tx_id, amount, recipient, resolved, account, bank in (
        (1, "tx-1", 10000, "Mum", "Mercy Johnson", "8162511023", "Opay"),
        (2, "tx-2", 5000, "Tolu", "Tolu Adedayo", "0760505261", "First Bank"),
        (3, "tx-3", 7000, "Dad", "Dad Ade", "0011223344", "UBA"),
    ):
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message=_group_message_with_size(tx_id, index, 3),
            task_type="transfer",
            payload=_leg_payload(
                amount=amount,
                recipient_name=recipient,
                resolved_name=resolved,
                account=account,
                bank=bank,
            ),
            locale="en",
        )
        txs[tx_id] = _tx(tx_id, amount=amount, recipient_name=resolved, bank_name=bank, account_number=account)

    worker = _worker(redis_client, txs)

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="all except the last one",
    )

    assert result.outcome == SupportOutcome.OK
    assert [job["transaction_reference"] for job in result.receipt_jobs] == ["tx-1", "tx-2"]


@pytest.mark.asyncio
async def test_support_worker_only_named_leg_selects_single_receipt() -> None:
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )
    worker = _worker(
        redis_client,
        {
            "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
            "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
        },
    )

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="only the one for Tolu",
    )

    assert result.outcome == SupportOutcome.OK
    assert len(result.receipt_jobs) == 1
    assert result.receipt_jobs[0]["transaction_reference"] == "tx-2"


@pytest.mark.asyncio
async def test_support_worker_other_one_uses_remaining_receipt_thread_candidate() -> None:
    redis_client = _RedisStub()
    txs = {
        "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
        "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
    }
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )
    worker = _worker(redis_client, txs)

    first = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="receipt for Mum",
    )
    second = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="also for the other one",
    )

    assert first.outcome == SupportOutcome.OK
    assert len(first.receipt_jobs) == 1
    assert first.receipt_jobs[0]["transaction_reference"] == "tx-1"
    assert second.outcome == SupportOutcome.OK
    assert len(second.receipt_jobs) == 1
    assert second.receipt_jobs[0]["transaction_reference"] == "tx-2"


@pytest.mark.asyncio
async def test_support_worker_remaining_ones_select_rest_after_first_receipt() -> None:
    redis_client = _RedisStub()
    txs = {}
    for index, tx_id, amount, recipient, resolved, account, bank in (
        (1, "tx-1", 10000, "Mum", "Mercy Johnson", "8162511023", "Opay"),
        (2, "tx-2", 5000, "Tolu", "Tolu Adedayo", "0760505261", "First Bank"),
        (3, "tx-3", 7000, "Dad", "Dad Ade", "0011223344", "UBA"),
    ):
        await record_group_leg_and_maybe_build_summary(
            redis_client,
            message=_group_message_with_size(tx_id, index, 3),
            task_type="transfer",
            payload=_leg_payload(
                amount=amount,
                recipient_name=recipient,
                resolved_name=resolved,
                account=account,
                bank=bank,
            ),
            locale="en",
        )
        txs[tx_id] = _tx(tx_id, amount=amount, recipient_name=resolved, bank_name=bank, account_number=account)
    worker = _worker(redis_client, txs)

    first = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="receipt for the first one",
    )
    second = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="the remaining ones",
    )

    assert first.outcome == SupportOutcome.OK
    assert len(first.receipt_jobs) == 1
    assert first.receipt_jobs[0]["transaction_reference"] == "tx-1"
    assert second.outcome == SupportOutcome.OK
    assert [job["transaction_reference"] for job in second.receipt_jobs] == ["tx-2", "tx-3"]


@pytest.mark.asyncio
async def test_support_worker_other_one_after_both_reports_already_sent() -> None:
    redis_client = _RedisStub()
    txs = {
        "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
        "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
    }
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )
    worker = _worker(redis_client, txs)

    first = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="Give the receipt for both",
    )
    second = await worker.run(
        payload={"intent": "receipt_request"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="Also for the other one",
    )

    assert first.outcome == SupportOutcome.OK
    assert [job["transaction_reference"] for job in first.receipt_jobs] == ["tx-1", "tx-2"]
    assert second.outcome == SupportOutcome.OK
    assert second.receipt_jobs == []
    assert "already sent" in (second.response or "").lower()


@pytest.mark.asyncio
async def test_support_worker_enqueues_all_receipts_for_recent_transfer_batch_in_order() -> None:
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )

    worker = _worker(
        redis_client,
        {
            "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
            "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
        },
    )

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={
            "phone_number": "2348162511023",
            "channel": "telegram",
            "channel_identity": "927331985",
            "language": "en",
        },
        user_message="Send all the receipts",
    )

    assert result.outcome == SupportOutcome.OK
    assert "receipts" in (result.response or "").lower()
    assert [job["transaction_reference"] for job in result.receipt_jobs] == ["tx-1", "tx-2"]


@pytest.mark.asyncio
async def test_support_worker_all_receipts_skips_non_successful_and_non_transfer_legs() -> None:
    redis_client = _RedisStub()
    base_message = {
        "async_group": {
            "async_group_id": "group-mixed",
            "async_group_size": 3,
            "async_group_kind": "mixed_batch",
            "async_group_index": 1,
        },
        "channel": "telegram",
        "channel_identity": "927331985",
        "phone_number": "2348162511023",
    }
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message={**base_message, "transaction_id": "tx-1", "async_group": {**base_message["async_group"], "async_group_index": 1}},
        task_type="transfer",
        payload=_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message={**base_message, "transaction_id": "tx-2", "async_group": {**base_message["async_group"], "async_group_index": 2}},
        task_type="transfer",
        payload={
            **_leg_payload(
                amount=5000,
                recipient_name="Tolu",
                resolved_name="Tolu Adedayo",
                account="0760505261",
                bank="First Bank",
            ),
            "final_status": "processing",
        },
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message={**base_message, "transaction_id": "tx-3", "async_group": {**base_message["async_group"], "async_group_index": 3}},
        task_type="airtime",
        payload={"amount": 2000, "network": "MTN", "phone_number": "2348012345678", "final_status": "success"},
        locale="en",
    )

    worker = _worker(
        redis_client,
        {
            "tx-1": _tx("tx-1", amount=10000, recipient_name="Mercy Johnson", bank_name="Opay", account_number="8162511023"),
            "tx-2": _tx(
                "tx-2",
                amount=5000,
                recipient_name="Tolu Adedayo",
                bank_name="First Bank",
                account_number="0760505261",
                status="pending",
            ),
        },
    )

    result = await worker.run(
        payload={"intent": "receipt_request"},
        context={
            "phone_number": "2348162511023",
            "channel": "telegram",
            "channel_identity": "927331985",
            "language": "en",
        },
        user_message="all receipts",
    )

    assert result.outcome == SupportOutcome.OK
    assert [job["transaction_reference"] for job in result.receipt_jobs] == ["tx-1"]
    assert "skipped" in (result.response or "").lower()
    assert "pending" in (result.response or "").lower()
    assert "non-transfer" in (result.response or "").lower()


@pytest.mark.asyncio
async def test_support_worker_answers_recent_failed_batch_reason_from_failure_category() -> None:
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_failed_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
            error_message="Provider down",
            failure_category="provider_unavailable",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )

    worker = _worker(
        redis_client,
        {
            "tx-1": _tx(
                "tx-1",
                amount=10000,
                recipient_name="Mercy Johnson",
                bank_name="Opay",
                account_number="8162511023",
                status="failed",
                error_message="Provider down",
            ),
            "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
        },
    )

    result = await worker.run(
        payload={"intent": "failed_transfer"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="why did it fail?",
    )

    assert result.outcome == SupportOutcome.OK
    assert "Provider down" in (result.response or "")
    assert "retry now" in (result.response or "").lower()


@pytest.mark.asyncio
async def test_support_worker_recent_failed_retry_uses_category_repair_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.chat.src.agent.graphs.support.capabilities.check_unsupported_actions",
        lambda domain, requested_actions: [],
    )
    redis_client = _RedisStub()
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-1", 1),
        task_type="transfer",
        payload=_failed_leg_payload(
            amount=10000,
            recipient_name="Mum",
            resolved_name="Mercy Johnson",
            account="8162511023",
            bank="Opay",
            error_message="Insufficient funds",
            failure_category="insufficient_funds",
        ),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=_group_message("tx-2", 2),
        task_type="transfer",
        payload=_leg_payload(
            amount=5000,
            recipient_name="Tolu",
            resolved_name="Tolu Adedayo",
            account="0760505261",
            bank="First Bank",
        ),
        locale="en",
    )

    worker = _worker(
        redis_client,
        {
            "tx-1": _tx(
                "tx-1",
                amount=10000,
                recipient_name="Mercy Johnson",
                bank_name="Opay",
                account_number="8162511023",
                status="failed",
                error_message="Insufficient funds",
            ),
            "tx-2": _tx("tx-2", amount=5000, recipient_name="Tolu Adedayo", bank_name="First Bank", account_number="0760505261"),
        },
    )

    result = await worker.run(
        payload={"intent": "retry_transfer"},
        context={"phone_number": "2348162511023", "channel_identity": "927331985", "language": "en"},
        user_message="retry failed one",
    )

    assert result.outcome == SupportOutcome.OK
    assert "another source account" in (result.response or "").lower()
