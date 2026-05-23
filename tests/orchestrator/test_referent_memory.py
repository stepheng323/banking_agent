from __future__ import annotations

import time

from apps.chat.src.agent.orchestrator.context.models import ContextEntity, ContextFrame, ContextFrameType, EntityType
from apps.chat.src.agent.orchestrator.context.referent_memory import (
    ReferentMemoryItem,
    build_resolved_referents,
    forget_stashed_referents,
    remember_referents_from_completed_task,
    remember_referents_from_frame,
    remember_referents_from_stashed_session,
    resolve_phone_reference,
    resolve_recipient_reference,
)
from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _state() -> OrchestratorState:
    return OrchestratorState(user_id="u_referent", phone_number="2348000000000", channel="whatsapp")


def test_referent_memory_stores_beneficiary_from_single_frame() -> None:
    state = _state()
    now = int(time.time())
    frame = ContextFrame(
        frame_id="bene_single",
        frame_type=ContextFrameType.BENEFICIARY_LIST,
        items=[
            ContextEntity(
                entity_type=EntityType.BENEFICIARY,
                entity_id="bene-1",
                label="Mum",
                data={
                    "id": "bene-1",
                    "alias": "Mum",
                    "account_name": "Mercy Johnson",
                    "account_number": "8162511023",
                    "bank_name": "Opay",
                    "pin": "1234",
                },
            )
        ],
        created_at_ts=now,
    )

    remember_referents_from_frame(state, frame)

    items = state.referent_memory.items
    assert {item.referent_type for item in items} >= {"beneficiary", "recipient"}
    recipient = next(item for item in items if item.referent_type == "recipient")
    assert recipient.data["account_number"] == "8162511023"
    assert "pin" not in recipient.data


def test_referent_memory_resolves_single_recipient_reference() -> None:
    state = _state()
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="completed_task",
            label="Emeka",
            data={"recipient_name": "Emeka", "recipient_account": "1234567890"},
            confidence=0.94,
        )
    ]

    result = resolve_recipient_reference(state, "send him 5k")

    assert result.status == "resolved"
    assert result.item is not None
    assert result.item.label == "Emeka"


def test_referent_memory_ambiguous_for_two_visible_recipients() -> None:
    state = _state()
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="context_frame",
            label="Mum",
            data={"recipient_name": "Mum", "recipient_account": "1111111111"},
            confidence=0.72,
        ),
        ReferentMemoryItem(
            referent_type="recipient",
            source="context_frame",
            label="Dad",
            data={"recipient_name": "Dad", "recipient_account": "2222222222"},
            confidence=0.72,
        ),
    ]

    result = resolve_recipient_reference(state, "send him 5k")

    assert result.status == "ambiguous"
    assert {item.label for item in result.candidates} == {"Dad", "Mum"}


def test_referent_memory_prunes_expired_items() -> None:
    state = _state()
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="completed_task",
            label="Expired",
            data={"recipient_name": "Expired"},
            created_at_ts=1,
            ttl_seconds=1,
        )
    ]

    result = resolve_recipient_reference(state, "send him 5k")

    assert result.status == "none"
    assert state.referent_memory.items == []


def test_completed_airtime_task_seeds_phone_referent() -> None:
    state = _state()
    task = TaskSpec(
        id="airtime-1",
        type="airtime",
        stage=TaskStage.COMPLETED,
        payload={"recipient_phone": "08012345678", "network": "MTN", "amount": 1000},
    )

    remember_referents_from_completed_task(state, task)
    result = resolve_phone_reference(state, "buy data for that number")

    assert result.status == "resolved"
    assert result.item is not None
    assert result.item.data["phone"] == "08012345678"


def test_stashed_transfer_seeds_safe_referents_with_stash_id() -> None:
    state = _state()
    session = {
        "stash_id": "stash-1",
        "stashed_at_ts": int(time.time()),
        "tasks": {
            "transfer-1": TaskSpec(
                id="transfer-1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={
                    "amount": 5000,
                    "recipient_name": "Grace",
                    "recipient_account": "8162511023",
                    "recipient_bank_name": "Opay",
                    "source_account_id": "acc-1",
                    "source_bank_name": "Kuda",
                    "pin": "1234",
                    "otp": "999999",
                },
            )
        },
    }

    remember_referents_from_stashed_session(state, session)

    types = {item.referent_type for item in state.referent_memory.items}
    assert {"recipient", "amount", "source_account", "transaction"}.issubset(types)
    for item in state.referent_memory.items:
        assert item.source == "stashed_session"
        assert item.data["stash_id"] == "stash-1"
        assert "pin" not in item.data
        assert "otp" not in item.data


def test_stashed_airtime_seeds_phone_amount_and_transaction_referents() -> None:
    state = _state()
    session = {
        "stash_id": "stash-airtime",
        "stashed_at_ts": int(time.time()),
        "tasks": {
            "airtime-1": TaskSpec(
                id="airtime-1",
                type="airtime",
                stage=TaskStage.EXTRACTED,
                payload={"amount": 2000, "recipient_phone": "08162511023", "network": "MTN"},
            )
        },
    }

    remember_referents_from_stashed_session(state, session)

    phone = next(item for item in state.referent_memory.items if item.referent_type == "phone")
    assert phone.data["phone"] == "08162511023"
    assert phone.data["network"] == "MTN"
    assert any(item.referent_type == "amount" for item in state.referent_memory.items)
    assert any(item.referent_type == "transaction" for item in state.referent_memory.items)


def test_forget_stashed_referents_clears_only_matching_stash() -> None:
    state = _state()
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="stashed_session",
            label="Grace",
            data={"recipient_name": "Grace", "stash_id": "stash-1"},
        ),
        ReferentMemoryItem(
            referent_type="recipient",
            source="stashed_session",
            label="Ada",
            data={"recipient_name": "Ada", "stash_id": "stash-2"},
        ),
        ReferentMemoryItem(
            referent_type="recipient",
            source="completed_task",
            label="Emeka",
            data={"recipient_name": "Emeka"},
        ),
    ]

    forget_stashed_referents(state, {"stash-1"})

    labels = {item.label for item in state.referent_memory.items}
    assert labels == {"Ada", "Emeka"}


def test_stashed_referent_does_not_replace_existing_completed_referent() -> None:
    state = _state()
    state.referent_memory.items = [
        ReferentMemoryItem(
            referent_type="recipient",
            source="completed_task",
            label="Grace",
            data={"recipient_name": "Grace", "recipient_account": "8162511023"},
        )
    ]
    session = {
        "stash_id": "stash-grace",
        "stashed_at_ts": int(time.time()),
        "tasks": {
            "transfer-1": TaskSpec(
                id="transfer-1",
                type="transfer",
                stage=TaskStage.EXTRACTED,
                payload={"recipient_name": "Grace", "recipient_account": "8162511023", "amount": 5000},
            )
        },
    }

    remember_referents_from_stashed_session(state, session)
    forget_stashed_referents(state, {"stash-grace"})

    assert len(state.referent_memory.items) == 1
    assert state.referent_memory.items[0].source == "completed_task"
    assert state.referent_memory.items[0].label == "Grace"


def test_build_resolved_referents_includes_only_referenced_types() -> None:
    state = _state()
    task = TaskSpec(
        id="transfer-1",
        type="transfer",
        stage=TaskStage.COMPLETED,
        payload={"recipient_name": "Emeka", "recipient_account": "1234567890", "amount": 5000},
    )

    remember_referents_from_completed_task(state, task)

    resolved = build_resolved_referents(state, "send him 5k")

    assert resolved["recipient"]["status"] == "resolved"
    assert "amount" not in resolved
