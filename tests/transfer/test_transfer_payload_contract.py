from banking.transfers.models.types import TransferPayload


def test_transfer_payload_normalizes_none_transfer_all_to_false() -> None:
    payload = TransferPayload(transfer_all=None)

    assert payload.transfer_all is False


def test_transfer_payload_normalizes_none_source_affinity_mode_to_auto() -> None:
    payload = TransferPayload(source_affinity_mode=None)

    assert payload.source_affinity_mode == "auto"
