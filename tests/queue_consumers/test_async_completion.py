from __future__ import annotations

from banking.transactions.runtime.async_completion import record_group_leg_and_maybe_build_summary
from banking.transactions.runtime.async_group_recent_batch import get_recent_batch_reference


class _RedisStub:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}

    async def hset(self, key: str, field: str, value: str) -> None:
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


def _message(
    *,
    transaction_id: str,
    async_group_id: str,
    async_group_index: int,
    async_group_size: int = 2,
    async_group_kind: str = "multi_transfer",
) -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "async_group": {
            "async_group_id": async_group_id,
            "async_group_size": async_group_size,
            "async_group_kind": async_group_kind,
            "async_group_index": async_group_index,
        },
    }


def _transfer_payload(*, amount: int, recipient: str, final_status: str) -> dict[str, object]:
    return {
        "amount": amount,
        "recipient_name": recipient,
        "recipient_resolved_name": recipient,
        "recipient_bank_name": "Opay",
        "recipient_account": "8162511023",
        "source_account_id": "acct-access",
        "source_account_number": "1234500003",
        "source_bank_name": "Access Bank",
        "source_affinity_mode": "explicit",
        "final_status": final_status,
    }


def _airtime_payload(
    *,
    amount: int,
    phone: str,
    final_status: str,
    error_message: str | None = None,
    failure_category: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "amount": amount,
        "phone_number": phone,
        "network": "MTN",
        "source_account_id": "acct-access",
        "source_account_number": "1234500003",
        "source_bank_name": "Access Bank",
        "source_affinity_mode": "explicit",
        "final_status": final_status,
    }
    if error_message:
        payload["error_message"] = error_message
    if failure_category:
        payload["failure_category"] = failure_category
    return payload


async def test_async_completion_latest_terminal_state_wins_before_finalization() -> None:
    redis_client = _RedisStub()
    first_leg = _message(transaction_id="tx-1", async_group_id="group-latest", async_group_index=1)
    second_leg = _message(transaction_id="tx-2", async_group_id="group-latest", async_group_index=2)

    initial = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=10000, recipient="Mum", final_status="failed"),
        locale="en",
    )
    assert initial is None

    overwritten = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=10000, recipient="Mum", final_status="success"),
        locale="en",
    )
    assert overwritten is None

    summary = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=second_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=6000, recipient="Gaines", final_status="success"),
        locale="en",
    )

    assert summary is not None
    assert summary["stage"] == "final"
    assert "Transfers Complete" in summary["text"]
    assert "Mum" in summary["text"]
    assert "Gaines" in summary["text"]
    assert "Transfers completed successfully" in summary["text"]
    assert "✗" not in summary["text"]


async def test_async_completion_duplicate_terminal_processing_emits_summary_once() -> None:
    redis_client = _RedisStub()
    first_leg = _message(transaction_id="tx-1", async_group_id="group-dedupe", async_group_index=1)
    second_leg = _message(transaction_id="tx-2", async_group_id="group-dedupe", async_group_index=2)

    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=10000, recipient="Mum", final_status="success"),
        locale="en",
    )

    first_summary = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=second_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=6000, recipient="Gaines", final_status="success"),
        locale="en",
    )
    duplicate_summary = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=second_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=6000, recipient="Gaines", final_status="success"),
        locale="en",
    )

    assert first_summary is not None
    assert first_summary["stage"] == "final"
    assert duplicate_summary is None


async def test_async_completion_mixed_batch_summary_waits_for_last_leg() -> None:
    redis_client = _RedisStub()
    transfer_leg = _message(
        transaction_id="tx-transfer",
        async_group_id="group-mixed",
        async_group_index=1,
        async_group_kind="mixed_batch",
    )
    airtime_leg = _message(
        transaction_id="tx-airtime",
        async_group_id="group-mixed",
        async_group_index=2,
        async_group_kind="mixed_batch",
    )
    transfer_leg["channel_identity"] = "927331985"
    airtime_leg["channel_identity"] = "927331985"

    first = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=transfer_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=10000, recipient="Mum", final_status="success"),
        locale="en",
    )
    summary = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=airtime_leg,
        task_type="airtime",
        payload=_airtime_payload(
            amount=2000,
            phone="08031234567",
            final_status="failed",
            error_message="Provider down",
            failure_category="provider_unavailable",
        ),
        locale="en",
    )

    assert first is None
    assert summary is not None
    assert summary["stage"] == "final"
    assert "Transaction Summary" in summary["text"]
    assert "Mum" in summary["text"]
    assert "08031234567" in summary["text"]
    assert "Reason: Provider down" in summary["text"]
    assert "Some transactions completed, but others failed." in summary["text"]
    actionable_payload = summary["actionable_payload"]
    assert actionable_payload["task_type"] == "batch"
    assert {item["task_type"] for item in actionable_payload["tasks"]} == {"transfer", "airtime"}
    airtime_payload = next(item for item in actionable_payload["tasks"] if item["task_type"] == "airtime")
    assert airtime_payload["recipient_phone"] == "08031234567"
    assert airtime_payload["action"] == "buy_airtime"
    assert airtime_payload["final_status"] == "failed"
    assert airtime_payload["error_message"] == "Provider down"
    assert airtime_payload["failure_category"] == "provider_unavailable"
    assert {item["source_account_number"] for item in actionable_payload["tasks"]} == {"1234500003"}
    assert {item["source_affinity_mode"] for item in actionable_payload["tasks"]} == {"explicit"}

    recent = await get_recent_batch_reference(redis_client, identity="927331985")
    assert recent is not None
    failed_leg = next(item for item in recent["legs"] if item["task_type"] == "airtime")
    assert failed_leg["final_status"] == "failed"
    assert failed_leg["error_message"] == "Provider down"
    assert failed_leg["failure_category"] == "provider_unavailable"


async def test_async_completion_transfer_summary_sends_initial_then_final_update() -> None:
    redis_client = _RedisStub()
    first_leg = _message(transaction_id="tx-1", async_group_id="group-processing", async_group_index=1)
    second_leg = _message(transaction_id="tx-2", async_group_id="group-processing", async_group_index=2)

    first = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=10000, recipient="Mum", final_status="success"),
        locale="en",
    )
    summary = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=second_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=6000, recipient="Tolu", final_status="processing"),
        locale="en",
    )

    assert first is None
    assert summary is not None
    assert summary["stage"] == "initial"
    assert "✓ ₦10,000 → Mum" in summary["text"]
    assert "… ₦6,000 → Tolu" in summary["text"]
    assert "You'll be notified when the final update arrives." in summary["text"]

    final_summary = await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=second_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=6000, recipient="Tolu", final_status="failed"),
        locale="en",
    )

    assert final_summary is not None
    assert final_summary["stage"] == "final"
    assert "Transaction Summary" in final_summary["text"] or "Transfers Complete" in final_summary["text"]
    assert "✓ ₦10,000 → Mum" in final_summary["text"]
    assert "✗ ₦6,000 → Tolu" in final_summary["text"]


async def test_async_completion_stores_recent_batch_reference_by_delivery_identity() -> None:
    redis_client = _RedisStub()
    first_leg = _message(transaction_id="tx-1", async_group_id="group-recent", async_group_index=1)
    first_leg["channel_identity"] = "927331985"
    second_leg = _message(transaction_id="tx-2", async_group_id="group-recent", async_group_index=2)
    second_leg["channel_identity"] = "927331985"

    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=first_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=10000, recipient="Mum", final_status="success"),
        locale="en",
    )
    await record_group_leg_and_maybe_build_summary(
        redis_client,
        message=second_leg,
        task_type="transfer",
        payload=_transfer_payload(amount=6000, recipient="Tolu", final_status="success"),
        locale="en",
    )

    recent = await get_recent_batch_reference(redis_client, identity="927331985")

    assert recent is not None
    assert recent["async_group_id"] == "group-recent"
    assert len(recent["legs"]) == 2
    assert recent["legs"][1]["recipient_name"] == "Tolu"
    assert recent["legs"][1]["receipt_allowed"] is True
