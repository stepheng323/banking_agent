from apps.chat.src.agent.orchestrator.models.domain import TransferPayload


def test_orchestrator_transfer_payload_normalizes_none_transfer_all_to_false() -> None:
    payload = TransferPayload(transfer_all=None)

    assert payload.transfer_all is False
