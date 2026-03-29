from apps.core.src.agent.graphs.transfer.models.types import TransferPayload


def test_transfer_payload_normalizes_none_transfer_all_to_false() -> None:
    payload = TransferPayload(transfer_all=None)

    assert payload.transfer_all is False
