"""Tests for Soul policy loading and adapters."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.core.src.agent.graphs.account.worker import AccountWorker
from apps.core.src.agent.graphs.airtime.worker import AirtimeWorker
from apps.core.src.agent.graphs.data.worker import DataWorker
from apps.core.src.agent.graphs.query import capabilities as query_capabilities
from apps.core.src.agent.graphs.transfer.worker import TransferWorker
from apps.core.src.agent.orchestrator.models.domain import AccountOutcome, TransactionOutcome
from apps.core.src.agent.orchestrator.nodes.planner import _build_policy_notice
from shared.policy import (
    build_planner_policy_block,
    get_cached_policy,
    load_policy,
    load_soul_policy,
    resolve_capability_message,
    resolve_capability_rule,
    validate_policy_coverage,
)

POLICY_PATH = "config/soul_policy.json"


class _DummyLLM:
    """Minimal stub for AccountWorker tests."""

    def with_structured_output(self, _schema: Any) -> Any:
        raise NotImplementedError


class _DummyRepo:
    """Minimal stub for worker constructor."""

    async def get_by_user(self, _user_id: str) -> list[Any]:
        return []


class _DummyBankingProvider:
    """Minimal stub for worker constructor."""

    async def get_balance(self, _account_id: str) -> None:
        return None


async def test_account_action_level_capability_gate() -> None:
    """Unsupported action in payload should be blocked deterministically."""
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


def test_policy_loads_from_json_file() -> None:
    """Canonical JSON policy should load into a validated policy model."""
    policy = load_policy(POLICY_PATH)
    assert policy.identity.name == "Narya AI"
    assert "Send money" in policy.supported_domains
    validate_policy_coverage(policy)


def test_policy_raises_when_file_missing() -> None:
    """Missing file should raise in strict single-source mode."""
    with pytest.raises(Exception):
        get_cached_policy(path="missing-policy.json", force_reload=True)

    # Reset cache back to real policy for subsequent tests.
    get_cached_policy(path=POLICY_PATH, force_reload=True)


def test_policy_raises_when_json_invalid(tmp_path: Path) -> None:
    """Malformed JSON policy should raise."""
    bad_policy_path = tmp_path / "bad_policy.json"
    bad_policy_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(Exception):
        load_policy(str(bad_policy_path))


def test_policy_loader_does_not_parse_markdown_legacy_format(tmp_path: Path) -> None:
    """Loader should not accept embedded-json markdown documents."""
    legacy_path = tmp_path / "legacy_soul.md"
    legacy_path.write_text(
        "<!-- SOUL_POLICY_JSON_START -->\n```json\n{}\n```\n<!-- SOUL_POLICY_JSON_END -->\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        load_soul_policy(str(legacy_path))


def test_planner_policy_block_contains_guardrails() -> None:
    """Planner policy block should expose key policy constraints."""
    policy = get_cached_policy(path=POLICY_PATH, force_reload=True)
    block = build_planner_policy_block(policy)
    assert "SOUL POLICY" in block
    assert "Supported domains" in block
    assert "Unsupported capabilities" in block


def test_capability_resolution_uses_policy_matrix() -> None:
    """Capability lookups should resolve from policy matrix."""
    message = resolve_capability_message(domain="support", action="retry_payout")
    rule = resolve_capability_rule(domain="account", action="close_account")

    assert message is not None
    assert "support ticket" in message.lower()
    assert rule is not None
    assert rule.supported is False


def test_policy_notice_acknowledges_supported_and_unsupported_mix() -> None:
    """Mixed request should acknowledge both supported and unsupported parts."""
    planner_output = SimpleNamespace(tasks=[SimpleNamespace(executor="transfer")])
    notice = _build_policy_notice("send 10k to tolu and invest 10k", planner_output)

    assert notice is not None
    assert "money transfer" in notice
    assert "Investments" in notice
    assert "send money or review recent transactions" in notice


def test_policy_detection_uses_runtime_policy_rules(tmp_path: Path) -> None:
    """Detection behavior should follow policy JSON edits without code changes."""
    raw = load_policy(POLICY_PATH).model_dump()
    raw["unsupported_detection"]["Investments"] = ["portfolio"]

    test_path = tmp_path / "soul_custom.json"
    json_payload = json.dumps(raw, ensure_ascii=True, indent=2)
    test_path.write_text(json_payload, encoding="utf-8")

    get_cached_policy(path=str(test_path), force_reload=True)
    planner_output = SimpleNamespace(tasks=[SimpleNamespace(executor="transfer")])

    notice = _build_policy_notice("send 10k to tolu and portfolio 10k", planner_output)
    assert notice is not None
    assert "Investments" in notice

    # Restore cache to default project policy.
    get_cached_policy(path=POLICY_PATH, force_reload=True)


def test_policy_validation_raises_when_required_action_missing() -> None:
    """Required account/support actions must exist in policy matrix."""
    base = load_policy(POLICY_PATH)
    raw = base.model_dump()
    del raw["capability_matrix"]["support"]["actions"]["create_ticket"]
    policy = base.__class__.model_validate(raw)

    with pytest.raises(ValueError):
        validate_policy_coverage(policy)


def test_query_capability_checks_use_policy_rules() -> None:
    """Query capability checks should be policy-backed."""
    missing = query_capabilities.check_capabilities(
        [query_capabilities.QueryCapability.TIME_ALL, query_capabilities.QueryCapability.FILTER_RECIPIENT]
    )
    assert query_capabilities.QueryCapability.TIME_ALL in missing
    assert query_capabilities.QueryCapability.FILTER_RECIPIENT not in missing


def test_query_limitation_message_prefers_policy_text() -> None:
    """Query limitation messaging should resolve from policy first."""
    message = query_capabilities.generate_limitation_message(
        [query_capabilities.QueryCapability.SEARCH_NARRATION_FUZZY]
    )
    assert "isn't available yet" in message.lower()
    assert "keyword search" in message.lower()


async def test_transfer_worker_blocks_unsupported_action_from_policy() -> None:
    """Transfer worker should fail fast on unsupported policy action."""
    worker = TransferWorker(
        validation_service=None,
        publisher=None,
        extractor=None,
        banking_provider=None,
        bank_cache=None,
        transaction_repo=None,
    )

    result = await worker.run(
        payload={"action": "schedule_transfer", "amount": 10000, "recipient_name": "Tolu"},
        context={"phone_number": "2348000000000"},
    )

    assert result.outcome == TransactionOutcome.FAILED
    assert result.error is not None
    assert "isn't available yet" in result.error.lower()
    assert "send money" in result.error.lower()


async def test_airtime_worker_blocks_unknown_action_from_policy() -> None:
    """Airtime worker should block actions not allowed by policy."""
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
    """Data worker should block actions not allowed by policy."""
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


def test_policy_file_exists_and_parses() -> None:
    """CI guardrail: canonical policy file must exist and parse."""
    policy_file = Path(POLICY_PATH)
    assert policy_file.exists(), f"Missing canonical policy file: {POLICY_PATH}"
    payload = json.loads(policy_file.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)


def test_soul_markdown_has_no_legacy_policy_markers() -> None:
    """CI guardrail: soul.md should not embed runtime policy JSON."""
    soul_text = Path("soul.md").read_text(encoding="utf-8")
    assert "SOUL_POLICY_JSON_START" not in soul_text
    assert "SOUL_POLICY_JSON_END" not in soul_text
