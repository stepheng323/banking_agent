from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import pytest

import shared.cache.llm_response_cache as llm_response_cache_module
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_observability import invoke_structured_prompt
from shared.config.settings import settings
from shared.observability.events import emit_operational_event
from shared.observability.llm import build_llm_runnable_config
from shared.observability.llm_call_metrics import (
    start_llm_call_recording,
    stop_llm_call_recording,
    structured_output_metrics,
)
from shared.observability.llm_provider_metadata import extract_provider_llm_metadata
from shared.observability.redaction import redacted_dict
from shared.types.planner import (
    DataTaskParameters,
    InterruptRouteDecision,
    PlannerOutput,
    SemanticRouteDecision,
    TransferTaskParameters,
    make_planned_task,
)
from shared.utils.json import to_json_safe


class ExampleEnum(str, Enum):
    VALUE = "value"


class _FakeStructuredLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    async def ainvoke(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        del messages
        self.calls += 1
        return self.payload


class _FakeRawStructuredLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    async def ainvoke(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        del messages
        self.calls += 1
        self.last_kwargs = kwargs
        return self.payload


class _FakeRawMessage:
    def __init__(
        self,
        *,
        usage_metadata: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata


class _FakeModelLLM:
    model_name = "gpt-test"


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.setex_calls.append((key, ttl, value))

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)


class _Logger:
    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append((event, kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        del event, kwargs


def _last_info_fields(logger: _Logger, event_name: str) -> dict[str, Any]:
    for event, fields in reversed(logger.infos):
        if event == event_name:
            return fields
    raise AssertionError(f"info event not found: {event_name}")


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


def test_operational_event_uses_supplied_logger_and_redacts_payload() -> None:
    logger = _Logger()

    emit_operational_event(
        "example_event",
        severity="info",
        domain="test",
        identifiers={"account_number": "1234567890"},
        details={"message": "pin 1234"},
        logger=logger,
    )

    assert len(logger.infos) == 1
    event, fields = logger.infos[0]
    assert event == "operational_event"
    assert fields["event_name"] == "example_event"
    assert fields["identifiers"]["account_number"] == "****7890"
    assert "1234" not in str(fields["details"])
    assert logger.warnings == []
    assert logger.errors == []


def test_operational_event_maps_warning_and_high_severity_to_supplied_logger() -> None:
    logger = _Logger()

    emit_operational_event("warning_event", severity="warning", domain="test", logger=logger)
    emit_operational_event("high_event", severity="high", domain="test", logger=logger)

    assert logger.warnings[0][1]["event_name"] == "warning_event"
    assert logger.errors[0][1]["event_name"] == "high_event"
    assert logger.infos == []


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


@pytest.mark.asyncio
async def test_invoke_structured_prompt_logs_output_json_metrics() -> None:
    logger = _Logger()
    llm = _FakeStructuredLLM({"decision": "domain_query", "confidence": 0.9, "target_intent": "query"})

    decision = await invoke_structured_prompt(
        llm,
        SemanticRouteDecision,
        system_prompt="system",
        user_prompt="user",
        logger=logger,
        event_name="semantic_router_llm_call",
        model_llm=_FakeModelLLM(),
    )

    fields = _last_info_fields(logger, "semantic_router_llm_call")
    compact_json = decision.model_dump_json(exclude_none=True, exclude_defaults=True, exclude_unset=True)
    assert fields["output_json_chars"] == len(compact_json)
    assert fields["output_expanded_json_chars"] == len(decision.model_dump_json())
    assert fields["output_token_estimate"] > 0
    assert fields["prompt_token_estimate"] > 0
    assert fields["output_compact_json_chars"] == fields["output_json_chars"]
    assert fields["output_expanded_json_chars"] >= fields["output_json_chars"]
    assert fields["output_default_overhead_chars"] >= 0
    assert fields["output_top_field_chars"]
    assert fields["response_schema_json_chars"] > 0
    assert fields["response_schema_token_estimate"] > 0
    assert fields["response_schema_defs_count"] >= 0


def test_structured_output_metrics_reports_planner_shape_without_values() -> None:
    output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t1",
                executor="transfer",
                action="send_money",
                instruction="Send money",
                risk="MONEY_MOVE",
                parameters=TransferTaskParameters(amount="10000", recipient_name="Tolu Access"),
            ),
            make_planned_task(
                task_id="t2",
                executor="data",
                action="buy_data",
                instruction="Buy data",
                risk="MONEY_MOVE",
                parameters=DataTaskParameters(plan="1GB", network="MTN", is_self=True),
            ),
        ],
        notes="short note",
    )

    metrics = structured_output_metrics(output)

    assert metrics["output_compact_json_chars"] == metrics["output_json_chars"]
    assert metrics["output_expanded_json_chars"] > metrics["output_json_chars"]
    assert metrics["output_default_overhead_chars"] > 0
    assert metrics["output_null_field_count"] > 0
    assert metrics["planner_task_count"] == 2
    assert metrics["planner_task_shapes"][0]["executor"] == "transfer"
    assert metrics["planner_task_shapes"][0]["parameter_set_field_count"] == 2
    assert metrics["planner_task_shapes"][0]["parameter_null_set_count"] == 0
    assert metrics["planner_task_shapes"][0]["parameter_set_keys"] == ["amount", "recipient_name"]
    assert metrics["planner_task_shapes"][0]["active_parameter_keys"] == ["amount", "recipient_name"]
    assert metrics["planner_task_shapes"][1]["parameter_set_keys"] == ["is_self", "network", "plan"]
    assert metrics["planner_task_shapes"][1]["active_parameter_keys"] == ["is_self", "network", "plan"]
    rendered = str(metrics["planner_task_shapes"])
    assert "Tolu Access" not in rendered
    assert "10000" not in rendered
    assert "MTN" not in rendered


def test_extract_provider_llm_metadata_reads_usage_metadata_shape() -> None:
    raw = _FakeRawMessage(
        usage_metadata={
            "input_tokens": 1200,
            "output_tokens": 100,
            "total_tokens": 1300,
            "input_token_details": {"cached_tokens": 800},
            "output_token_details": {"reasoning": 12},
        },
        response_metadata={
            "system_fingerprint": "fp_test",
            "finish_reason": "stop",
            "headers": {"x-request-id": "req_test"},
        },
    )

    fields = extract_provider_llm_metadata(raw)

    assert fields["provider_input_tokens"] == 1200
    assert fields["provider_output_tokens"] == 100
    assert fields["provider_total_tokens"] == 1300
    assert fields["provider_cached_tokens"] == 800
    assert fields["provider_cache_hit_rate"] == 0.6667
    assert fields["provider_reasoning_tokens"] == 12
    assert fields["provider_system_fingerprint"] == "fp_test"
    assert fields["provider_finish_reason"] == "stop"
    assert fields["provider_request_id"] == "req_test"


def test_extract_provider_llm_metadata_reads_token_usage_shape() -> None:
    raw = _FakeRawMessage(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 80,
                "total_tokens": 1080,
                "prompt_tokens_details": {"cached_tokens": 256},
                "completion_tokens_details": {"reasoning_tokens": 7},
            }
        }
    )

    fields = extract_provider_llm_metadata(raw)

    assert fields["provider_input_tokens"] == 1000
    assert fields["provider_output_tokens"] == 80
    assert fields["provider_total_tokens"] == 1080
    assert fields["provider_cached_tokens"] == 256
    assert fields["provider_cache_hit_rate"] == 0.256
    assert fields["provider_reasoning_tokens"] == 7


def test_extract_provider_llm_metadata_omits_missing_values() -> None:
    assert extract_provider_llm_metadata(_FakeRawMessage()) == {}


@pytest.mark.asyncio
async def test_invoke_structured_prompt_records_turn_llm_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_response_cache_enabled", False)
    logger = _Logger()
    llm = _FakeStructuredLLM({"decision": "domain_query", "confidence": 0.9, "target_intent": "query"})
    token = start_llm_call_recording()

    try:
        await invoke_structured_prompt(
            llm,
            SemanticRouteDecision,
            system_prompt="system",
            user_prompt="user",
            logger=logger,
            event_name="semantic_router_llm_call",
            model_llm=_FakeModelLLM(),
            path_label="direct_path",
            latency_span="semantic_router_llm",
            log_fields={"context_mode": "full"},
        )
    finally:
        records = stop_llm_call_recording(token)

    assert len(records) == 1
    assert records[0]["event_name"] == "semantic_router_llm_call"
    assert records[0]["llm_role"] == "semantic_router"
    assert records[0]["path_label"] == "direct_path"
    assert records[0]["latency_span"] == "semantic_router_llm"
    assert records[0]["prompt_token_estimate"] > 0
    assert records[0]["output_token_estimate"] > 0
    assert records[0]["output_compact_token_estimate"] > 0
    assert records[0]["output_expanded_token_estimate"] >= records[0]["output_token_estimate"]
    assert records[0]["output_default_overhead_chars"] >= 0
    assert records[0]["output_top_field_chars"]
    assert records[0]["context_mode"] == "full"
    assert records[0]["llm_call_index"] == 1
    assert records[0]["llm_event_call_index"] == 1
    assert records[0]["process_llm_call_index"] >= 1
    assert records[0]["process_llm_event_call_index"] >= 1
    assert records[0]["process_uptime_ms"] >= 0
    assert records[0]["response_schema_json_chars"] > 0


@pytest.mark.asyncio
async def test_invoke_structured_prompt_unwraps_raw_provider_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_response_cache_enabled", False)
    logger = _Logger()
    raw = _FakeRawMessage(
        usage_metadata={
            "input_tokens": 900,
            "output_tokens": 50,
            "total_tokens": 950,
            "input_token_details": {"cached_tokens": 600},
        },
        response_metadata={"headers": {"x-request-id": "req_raw"}},
    )
    llm = _FakeRawStructuredLLM(
        {
            "raw": raw,
            "parsed": {"decision": "domain_query", "confidence": 0.9, "target_intent": "query"},
            "parsing_error": None,
        }
    )
    token = start_llm_call_recording()

    try:
        decision = await invoke_structured_prompt(
            llm,
            SemanticRouteDecision,
            system_prompt="system",
            user_prompt="user",
            logger=logger,
            event_name="semantic_router_llm_call",
            model_llm=_FakeModelLLM(),
            prompt_cache_key="planner:transfer_only",
        )
    finally:
        records = stop_llm_call_recording(token)

    assert decision.decision == "domain_query"
    assert llm.last_kwargs == {"prompt_cache_key": "planner:transfer_only"}
    fields = _last_info_fields(logger, "semantic_router_llm_call")
    assert fields["provider_prompt_cache_key"] == "planner:transfer_only"
    assert fields["provider_input_tokens"] == 900
    assert fields["provider_output_tokens"] == 50
    assert fields["provider_total_tokens"] == 950
    assert fields["provider_cached_tokens"] == 600
    assert fields["provider_cache_hit_rate"] == 0.6667
    assert fields["provider_request_id"] == "req_raw"
    assert records[0]["provider_cached_tokens"] == 600
    assert records[0]["provider_prompt_cache_key"] == "planner:transfer_only"


@pytest.mark.asyncio
async def test_invoke_structured_prompt_raw_parsing_error_is_recorded() -> None:
    logger = _Logger()
    llm = _FakeRawStructuredLLM(
        {
            "raw": _FakeRawMessage(usage_metadata={"input_tokens": 10}),
            "parsed": None,
            "parsing_error": ValueError("bad parse"),
        }
    )
    token = start_llm_call_recording()

    try:
        with pytest.raises(ValueError, match="bad parse"):
            await invoke_structured_prompt(
                llm,
                SemanticRouteDecision,
                system_prompt="system",
                user_prompt="user",
                logger=logger,
                event_name="semantic_router_llm_call",
                model_llm=_FakeModelLLM(),
            )
    finally:
        records = stop_llm_call_recording(token)

    assert records[0]["error_type"] == "ValueError"
    assert records[0]["provider_input_tokens"] == 10


@pytest.mark.asyncio
async def test_structured_llm_cache_reuses_semantic_router_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(settings, "llm_response_cache_enabled", True)
    monkeypatch.setattr(settings, "llm_response_cache_ttl_seconds", 300)
    monkeypatch.setattr(settings, "llm_response_cache_types", ("SemanticRouteDecision",))
    monkeypatch.setattr(llm_response_cache_module.RedisClient, "get_client", staticmethod(lambda: redis))

    logger = _Logger()
    first_llm = _FakeStructuredLLM({"decision": "domain_query", "confidence": 0.9, "target_intent": "query"})
    second_llm = _FakeStructuredLLM({"decision": "domain_account", "confidence": 0.9, "target_intent": "account"})

    first = await invoke_structured_prompt(
        first_llm,
        SemanticRouteDecision,
        system_prompt="system",
        user_prompt="user",
        logger=logger,
        event_name="semantic_router_llm_call",
        model_llm=_FakeModelLLM(),
    )
    second = await invoke_structured_prompt(
        second_llm,
        SemanticRouteDecision,
        system_prompt="system",
        user_prompt="user",
        logger=logger,
        event_name="semantic_router_llm_call",
        model_llm=_FakeModelLLM(),
    )

    assert first.decision == "domain_query"
    assert second.decision == "domain_query"
    assert first_llm.calls == 1
    assert second_llm.calls == 0
    assert redis.setex_calls[0][1] == 300
    hit_fields = _last_info_fields(logger, "semantic_router_llm_call")
    assert hit_fields["cache_status"] == "hit"
    assert hit_fields["output_json_chars"] == len(
        second.model_dump_json(exclude_none=True, exclude_defaults=True, exclude_unset=True)
    )
    assert hit_fields["output_expanded_json_chars"] == len(second.model_dump_json())


@pytest.mark.asyncio
async def test_structured_llm_cache_skips_non_allowlisted_response_type(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(settings, "llm_response_cache_enabled", True)
    monkeypatch.setattr(settings, "llm_response_cache_types", ("SemanticRouteDecision",))
    monkeypatch.setattr(llm_response_cache_module.RedisClient, "get_client", staticmethod(lambda: redis))

    llm = _FakeStructuredLLM({"decision": "continue_flow", "confidence": 0.8})

    decision = await invoke_structured_prompt(
        llm,
        InterruptRouteDecision,
        system_prompt="system",
        user_prompt="user",
        logger=_Logger(),
        event_name="interrupt_router_llm_call",
        model_llm=_FakeModelLLM(),
    )

    assert decision.decision == "continue_flow"
    assert llm.calls == 1
    assert redis.setex_calls == []


@pytest.mark.asyncio
async def test_structured_llm_cache_invalid_payload_deletes_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(settings, "llm_response_cache_enabled", True)
    monkeypatch.setattr(settings, "llm_response_cache_types", ("SemanticRouteDecision",))
    monkeypatch.setattr(llm_response_cache_module.RedisClient, "get_client", staticmethod(lambda: redis))

    cache = llm_response_cache_module.StructuredLLMResponseCache()
    cache_key = cache.key(
        response_type=SemanticRouteDecision,
        model="gpt-test",
        system_prompt="system",
        user_prompt="user",
    )
    redis.values[cache_key] = '{"decision": 123}'
    llm = _FakeStructuredLLM({"decision": "domain_beneficiary", "confidence": 0.8, "target_intent": "beneficiary"})

    decision = await invoke_structured_prompt(
        llm,
        SemanticRouteDecision,
        system_prompt="system",
        user_prompt="user",
        logger=_Logger(),
        event_name="semantic_router_llm_call",
        model_llm=_FakeModelLLM(),
    )

    assert decision.decision == "domain_beneficiary"
    assert llm.calls == 1
    assert cache_key in redis.deleted
