from pathlib import Path

import pytest

from shared.utils.flow_decryption import get_private_key_from_env


def test_get_private_key_from_env_reads_inline_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_FLOW_PRIVATE_KEY", "line1\\nline2")

    assert get_private_key_from_env() == "line1\nline2"


def test_get_private_key_from_env_raises_when_value_is_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WHATSAPP_FLOW_PRIVATE_KEY", raising=False)

    with pytest.raises(FileNotFoundError, match="WHATSAPP_FLOW_PRIVATE_KEY is not set"):
        get_private_key_from_env()
