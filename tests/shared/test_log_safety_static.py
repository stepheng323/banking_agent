import re
from pathlib import Path


def test_provider_and_flow_paths_do_not_use_stdout_diagnostics() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative_path in (
        "shared/clients/telegram/client.py",
        "shared/clients/whatsapp/client.py",
        "apps/gateway/adapters/sender.py",
        "shared/utils/flow_encryption.py",
        "shared/utils/flow_decryption.py",
    ):
        text = (root / relative_path).read_text()
        assert re.search(r"\bprint\s*\(", text) is None
        assert "traceback.print_exc" not in text


def test_receipt_s3_client_does_not_enable_public_acl() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "shared/clients/storage/s3_client.py").read_text()
    assert "public-read" not in text
    assert "ACL=" not in text
