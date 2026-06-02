from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest

from shared.config.settings import settings
from shared.observability.llm import build_llm_runnable_config
from shared.observability.redaction import redacted_dict
from shared.utils.json import to_json_safe


class ExampleEnum(str, Enum):
    VALUE = "value"


def test_redaction_masks_sensitive_values() -> None:
    payload = {
        "account_number": "1234567890",
        "mandate_id": "mandate_abcdef1234567890",
        "message": "send from 08162511023 with pin 1234 and reference=mono_ref_123456789",
        "image_url": "data:image/png;base64,AAAA1111BBBB2222",
    }

    redacted = redacted_dict(payload)
    rendered = str(redacted)

    assert redacted["account_number"] == "****7890"
    assert str(redacted["mandate_id"]).startswith("hash:")
    assert "1234567890" not in rendered
    assert "08162511023" not in rendered
    assert "1234" not in rendered
    assert "mono_ref_123456789" not in rendered
    assert "AAAA1111BBBB2222" not in rendered


def test_json_safe_serialization_supports_operational_values() -> None:
    payload = {
        "amount_naira": Decimal("2000.50"),
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "created_at": datetime(2026, 6, 2, 10, 15, tzinfo=UTC),
        "day": date(2026, 6, 2),
        "status": ExampleEnum.VALUE,
    }

    assert to_json_safe(payload) == {
        "amount_naira": "2000.50",
        "id": "00000000-0000-0000-0000-000000000001",
        "created_at": "2026-06-02T10:15:00+00:00",
        "day": "2026-06-02",
        "status": "value",
    }


def test_json_safe_rejects_non_finite_float() -> None:
    with pytest.raises(ValueError, match="Non-finite float"):
        to_json_safe({"bad": float("nan")})


def test_llm_runnable_config_uses_safe_tags_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_observability_enabled", True)
    monkeypatch.setattr(settings, "llm_trace_sample_rate", 1.0)
    monkeypatch.setattr(settings, "llm_trace_privacy_mode", "masked")

    config = build_llm_runnable_config(
        role="planner",
        runtime="chat-worker",
        channel="telegram",
        path_label="semantic_router",
        phone_number="08162511023",
        channel_identity="telegram:08162511023",
        message_id="4303",
        turn_id="turn-1",
        locale="en-NG",
        semantic_path_shape="transfer>confirmation",
        task_domain="transfer",
        extra_metadata={"account_number": "1234567890", "prompt_preview": "pin 1234"},
    )

    assert "runtime:chat-worker" in config["tags"]
    assert "channel:telegram" in config["tags"]
    assert "llm_role:planner" in config["tags"]
    assert config["metadata"]["privacy_mode"] == "masked"
    assert config["metadata"]["phone_hash"]
    rendered = str(config)
    assert "08162511023" not in rendered
    assert "1234567890" not in rendered
    assert "1234" not in rendered


def test_llm_runnable_config_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_observability_enabled", False)

    assert build_llm_runnable_config(role="planner", phone_number="08162511023") == {}
