from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.utils.actionable_payload import build_actionable_payload


def _task(payload: dict) -> TaskSpec:
    return TaskSpec(
        id="t1",
        type="transfer",
        stage=TaskStage.EXTRACTED,
        payload=payload,
    )


def test_actionable_payload_returns_none_without_identifiers() -> None:
    task = _task({"amount": 5000, "recipient_name": "Mum"})
    assert build_actionable_payload(task) is None


def test_actionable_payload_preserves_expected_keys_when_identifiers_present() -> None:
    task = _task(
        {
            "idempotency_key": "idem-1",
            "transaction_id": "txn-1",
            "action": "send_money",
            "amount": 10000,
            "recipient_name": "Mum",
        }
    )

    payload = build_actionable_payload(task)

    assert payload is not None
    assert payload["idempotency_key"] == "idem-1"
    assert payload["task_id"] == "t1"
    assert payload["task_type"] == "transfer"
    assert payload["transaction_id"] == "txn-1"
    assert payload["action"] == "send_money"
    assert payload["amount"] == 10000
    assert payload["recipient_name"] == "Mum"


def test_actionable_payload_ignores_empty_string_values() -> None:
    task = _task(
        {
            "idempotency_key": "idem-2",
            "action": "send_money",
            "recipient_name": "",
            "network": "",
            "plan_name": "",
            "amount": 2000,
        }
    )

    payload = build_actionable_payload(task)

    assert payload is not None
    assert payload["amount"] == 2000
    assert "recipient_name" not in payload
    assert "network" not in payload
    assert "plan_name" not in payload


def test_actionable_payload_keeps_recipient_beneficiary_and_source_fields() -> None:
    task = _task(
        {
            "idempotency_key": "idem-3",
            "action": "send_money",
            "beneficiary_id": "bene-1",
            "recipient_name": "Mum",
            "recipient_resolved_name": "Mercy Johnson",
            "recipient_account": "8162511023",
            "recipient_bank_code": "999",
            "recipient_bank_name": "Opay",
            "source_bank_name": "Zenith Bank",
            "source_affinity_mode": "explicit",
            "resolved_from_saved_beneficiary": True,
            "final_status": "failed",
            "error_message": "Provider down",
            "failure_category": "provider_unavailable",
        }
    )

    payload = build_actionable_payload(task)

    assert payload is not None
    assert payload["beneficiary_id"] == "bene-1"
    assert payload["recipient_name"] == "Mum"
    assert payload["recipient_resolved_name"] == "Mercy Johnson"
    assert payload["recipient_account"] == "8162511023"
    assert payload["recipient_bank_code"] == "999"
    assert payload["recipient_bank_name"] == "Opay"
    assert payload["source_bank_name"] == "Zenith Bank"
    assert payload["source_affinity_mode"] == "explicit"
    assert payload["resolved_from_saved_beneficiary"] is True
    assert payload["final_status"] == "failed"
    assert payload["error_message"] == "Provider down"
    assert payload["failure_category"] == "provider_unavailable"
