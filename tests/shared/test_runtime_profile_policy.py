"""Tests for assistant profile, capability policy, and guardrails config."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.chat.src.agent.graphs.account.worker import AccountWorker
from apps.chat.src.agent.graphs.airtime.worker import AirtimeWorker
from apps.chat.src.agent.graphs.data.worker import DataWorker
from apps.chat.src.agent.graphs.query import capabilities as query_capabilities
from apps.chat.src.agent.graphs.transfer.worker import TransferWorker
from apps.chat.src.agent.orchestrator.models.domain import AccountOutcome, TransactionOutcome
from apps.chat.src.agent.orchestrator.nodes.planner.policy import _build_policy_notice
from shared.assistant_profile.loader import get_cached_assistant_profile, load_assistant_profile
from shared.assistant_profile.voice import build_planner_voice_block, get_runtime_voice
from shared.config.settings import settings
from shared.guardrails.loader import get_cached_guardrails, load_guardrails
from shared.policy.adapters import resolve_capability_message, resolve_capability_rule
from shared.policy.loader import get_cached_policy, load_policy
from shared.policy.validation import validate_policy_coverage

ASSISTANT_PROFILE_PATH = "config/assistant_profile.json"
CAPABILITY_POLICY_PATH = "config/capability_policy.json"
DOMAIN_GUARDRAILS_PATH = "config/domain_guardrails.json"


class _DummyLLM:
    def with_structured_output(self, _schema: Any) -> Any:
        raise NotImplementedError


class _DummyRepo:
    async def get_by_user(self, _user_id: str) -> list[Any]:
        return []


class _DummyBankingProvider:
    async def get_balance(self, _account_id: str) -> None:
        return None


async def test_account_action_level_capability_gate() -> None:
    worker = AccountWorker(
        account_repo=_DummyRepo(),
        user_repo=_DummyRepo(),
        llm=_DummyLLM(),
        banking_provider=_DummyBankingProvider(),
        session_manager=None,
        direct_debit_provider=None,
    )

    result = await worker.run(
        payload={"action": "close_account"},
        context={"profile": {"id": "u_1"}, "language": "en"},
        user_message=None,
    )

    assert result.outcome == AccountOutcome.OK
    assert result.response is not None
    assert "isn't available yet" in result.response.lower()
    assert "unlink account" in result.response.lower()


def test_capability_policy_loads_from_json_file() -> None:
    policy = load_policy(CAPABILITY_POLICY_PATH)
    assert "transfer" in policy.capability_matrix
    validate_policy_coverage(policy)


def test_assistant_profile_loads_from_json_file() -> None:
    profile = load_assistant_profile(ASSISTANT_PROFILE_PATH)
    assert profile.identity.name == settings.app_name
    assert "Send money" in profile.supported_domains


def test_guardrails_load_from_json_file() -> None:
    guardrails = load_guardrails(DOMAIN_GUARDRAILS_PATH)
    assert guardrails.transfer.dynamic_risk.floor_amount == 50000
    assert guardrails.query.max_lookback_days == 180


def test_capability_policy_raises_when_file_missing() -> None:
    with pytest.raises(Exception):
        get_cached_policy(path="missing-policy.json", force_reload=True)
    get_cached_policy(path=CAPABILITY_POLICY_PATH, force_reload=True)


def test_assistant_profile_raises_when_json_invalid(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad_profile.json"
    bad_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(Exception):
        load_assistant_profile(str(bad_path))


def test_planner_profile_summary_is_compact_and_grounded() -> None:
    profile = get_cached_assistant_profile(path=ASSISTANT_PROFILE_PATH, force_reload=True)
    summary = build_planner_voice_block(profile)
    assert "Supported(profile): Send money" in summary
    assert "Unsupported(profile): Financial advice" in summary
    assert "conversational.out_of_scope" in summary
    assert profile.tone.response_rules[0][:10] in summary
    assert profile.safety_rules[0][:10] in summary
    assert len(summary) < 400


def test_planner_profile_summary_uses_input_profile_values() -> None:
    profile = load_assistant_profile(ASSISTANT_PROFILE_PATH).model_copy(deep=True)
    profile.supported_domains = ["Card freeze", "Send money"]
    profile.unsupported_capabilities = ["Crypto staking", "Investments"]
    profile.tone.response_rules = ["State limits directly"]
    profile.safety_rules = ["Banking tasks only"]

    summary = build_planner_voice_block(profile)
    assert "Supported(profile): Card freeze(+1)" in summary
    assert "Unsupported(profile): Crypto staking(+1)" in summary
    assert "State limits directly" in summary
    assert "Banking tasks only" in summary


def test_runtime_voice_uses_assistant_profile_as_single_voice_source() -> None:
    profile = load_assistant_profile(ASSISTANT_PROFILE_PATH)
    voice = get_runtime_voice(profile=profile)

    assert voice.name == profile.identity.name
    assert voice.description == profile.identity.description
    assert voice.tone_style == profile.tone.style
    assert voice.brevity == profile.tone.brevity
    assert voice.response_rules == tuple(profile.tone.response_rules)
    assert voice.safety_rules == tuple(profile.safety_rules)
    assert voice.as_meta_payload()["supported_domains"] == profile.supported_domains


def test_planner_prompt_refresh_reloads_profile_summary_block(tmp_path: Path) -> None:
    from shared.services import task_planner_prompts

    raw = load_assistant_profile(ASSISTANT_PROFILE_PATH).model_dump()
    raw["supported_domains"][0] = "Card freeze"
    raw["unsupported_capabilities"][0] = "Crypto staking"
    raw["tone"]["response_rules"] = ["State limits directly"]
    raw["safety_rules"] = ["Banking tasks only"]

    custom_path = tmp_path / "assistant_profile_custom.json"
    custom_path.write_text(json.dumps(raw, ensure_ascii=True, indent=2), encoding="utf-8")

    try:
        get_cached_assistant_profile(path=str(custom_path), force_reload=True)
        task_planner_prompts.refresh_planner_system_prompt()
        profile_block = task_planner_prompts.PLANNER_POLICY_BLOCK
        assert "Supported(profile): Card freeze(+6)" in profile_block
        assert "Unsupported(profile): Crypto staking(+5)" in profile_block
        assert "State limits directly" in profile_block
        assert "Banking tasks only" in profile_block
    finally:
        get_cached_assistant_profile(path=ASSISTANT_PROFILE_PATH, force_reload=True)
        task_planner_prompts.refresh_planner_system_prompt()


def test_capability_resolution_uses_policy_matrix() -> None:
    message = resolve_capability_message(domain="support", action="retry_payout")
    rule = resolve_capability_rule(domain="account", action="close_account")

    assert message is not None
    assert "support ticket" in message.lower()
    assert rule is not None
    assert rule.supported is False


def test_policy_notice_acknowledges_supported_and_unsupported_mix() -> None:
    planner_output = SimpleNamespace(tasks=[SimpleNamespace(executor="transfer")])
    notice = _build_policy_notice("send 10k to tolu and invest 10k", planner_output)

    assert notice is not None
    assert "money transfer" in notice
    assert "investments or crypto" in notice
    assert "send money or review recent transactions" in notice


def test_policy_notice_localizes_unsupported_registry_labels() -> None:
    planner_output = SimpleNamespace(tasks=[SimpleNamespace(executor="transfer")])
    notice = _build_policy_notice("send 10k to tolu and ra bitcoin", planner_output, locale="yo")

    assert notice is not None
    assert "transfer owo" in notice
    assert "idoko owo tabi crypto" in notice
    assert "transfer owo or wiwa recent transactions" in notice


def test_policy_validation_raises_when_required_action_missing() -> None:
    base = load_policy(CAPABILITY_POLICY_PATH)
    raw = base.model_dump()
    del raw["capability_matrix"]["support"]["actions"]["create_ticket"]
    policy = base.__class__.model_validate(raw)

    with pytest.raises(ValueError):
        validate_policy_coverage(policy)


def test_policy_validation_rejects_unsupported_alternative_target() -> None:
    base = load_policy(CAPABILITY_POLICY_PATH)
    raw = base.model_dump()
    raw["capability_matrix"]["query"]["actions"]["search_narration_fuzzy"]["alternative"] = "export_pdf"
    policy = base.__class__.model_validate(raw)

    with pytest.raises(ValueError):
        validate_policy_coverage(policy)


def test_query_capability_checks_use_policy_rules() -> None:
    missing = query_capabilities.check_capabilities(
        [query_capabilities.QueryCapability.TIME_ALL, query_capabilities.QueryCapability.FILTER_RECIPIENT]
    )
    assert query_capabilities.QueryCapability.TIME_ALL in missing
    assert query_capabilities.QueryCapability.FILTER_RECIPIENT not in missing


def test_query_limitation_message_prefers_policy_text() -> None:
    message = query_capabilities.generate_limitation_message(
        [query_capabilities.QueryCapability.SEARCH_NARRATION_FUZZY]
    )
    assert "isn't available yet" in message.lower()
    assert "keyword search" in message.lower()


async def test_transfer_worker_blocks_unsupported_action_from_policy() -> None:
    worker = TransferWorker(
        validation_service=None,
        publisher=None,
        extractor=None,
        resolver_provider=None,
        bank_cache=None,
        transaction_repo=None,
    )

    result = await worker.run(
        payload={"action": "international_transfer", "amount": 10000, "recipient_name": "Tolu"},
        context={"phone_number": "2348000000000"},
    )

    assert result.outcome == TransactionOutcome.FAILED
    assert result.error is not None
    assert "can't send internationally" in result.error.lower()
    assert "send money" in result.error.lower()


async def test_airtime_worker_blocks_unknown_action_from_policy() -> None:
    worker = AirtimeWorker(
        extractor=None,
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={"action": "refund_airtime", "amount": 1000},
        context={"phone_number": "2348000000000"},
    )

    assert result.outcome == TransactionOutcome.FAILED
    assert result.error is not None
    assert "isn't available yet" in result.error.lower()


async def test_data_worker_blocks_unknown_action_from_policy() -> None:
    worker = DataWorker(
        extractor=None,
        bill_provider=None,
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={"action": "refund_data", "amount": 1000},
        context={"phone_number": "2348000000000"},
    )

    assert result.outcome == TransactionOutcome.FAILED
    assert result.error is not None
    assert "isn't available yet" in result.error.lower()


def test_split_config_files_exist_and_parse() -> None:
    for path in (ASSISTANT_PROFILE_PATH, CAPABILITY_POLICY_PATH, DOMAIN_GUARDRAILS_PATH):
        config_file = Path(path)
        assert config_file.exists(), f"Missing canonical config file: {path}"
        payload = json.loads(config_file.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)


def test_legacy_soul_policy_json_removed() -> None:
    assert not Path("config/soul_policy.json").exists()


def test_soul_markdown_has_no_legacy_policy_markers() -> None:
    soul_text = Path("soul.md").read_text(encoding="utf-8")
    assert "SOUL_POLICY_JSON_START" not in soul_text
    assert "SOUL_POLICY_JSON_END" not in soul_text


def test_guardrails_cache_loads_runtime_split() -> None:
    guardrails = get_cached_guardrails(path=DOMAIN_GUARDRAILS_PATH, force_reload=True)
    assert guardrails.support.max_escalation_attempts == 3
