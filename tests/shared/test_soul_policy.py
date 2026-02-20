"""Tests for Soul policy loading and adapters."""

from types import SimpleNamespace

from apps.core.src.agent.graphs.account.worker import AccountWorker
from apps.core.src.agent.orchestrator.models.domain import AccountOutcome
from apps.core.src.agent.orchestrator.nodes.planner import _build_policy_notice
from shared.policy import (
    build_planner_policy_block,
    get_cached_policy,
    load_soul_policy,
    resolve_capability_message,
    resolve_capability_rule,
)


class _DummyLLM:
    """Minimal stub for AccountWorker tests."""

    def with_structured_output(self, _schema):
        raise NotImplementedError


class _DummyRepo:
    """Minimal stub for worker constructor."""

    async def get_by_user(self, _user_id):
        return []


class _DummyBankingProvider:
    """Minimal stub for worker constructor."""

    async def get_balance(self, _account_id):
        return None


async def test_account_action_level_capability_gate():
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
    assert "close bank accounts" in result.response.lower()


def test_policy_loads_from_soul_md():
    """soul.md should load into a validated policy model."""
    policy = load_soul_policy("soul.md")
    assert policy.identity.name == "Fusepay"
    assert "Send money" in policy.supported_domains


def test_policy_fallback_when_file_missing():
    """Missing file should fallback safely via cache helper."""
    policy = get_cached_policy(path="missing-soul.md", force_reload=True)
    assert policy.identity.name == "Fusepay"
    assert policy.version == "1.0.0"

    # Reset cache back to real policy for subsequent tests.
    get_cached_policy(path="soul.md", force_reload=True)


def test_planner_policy_block_contains_guardrails():
    """Planner policy block should expose key policy constraints."""
    policy = get_cached_policy(path="soul.md", force_reload=True)
    block = build_planner_policy_block(policy)
    assert "SOUL POLICY" in block
    assert "Supported domains" in block
    assert "Unsupported capabilities" in block


def test_capability_resolution_uses_policy_matrix():
    """Capability lookups should resolve from policy matrix."""
    message = resolve_capability_message(domain="support", action="retry_payout")
    rule = resolve_capability_rule(domain="account", action="close_account")

    assert message is not None
    assert "support ticket" in message.lower()
    assert rule is not None
    assert rule.supported is False


def test_policy_notice_acknowledges_supported_and_unsupported_mix():
    """Mixed request should acknowledge both supported and unsupported parts."""
    planner_output = SimpleNamespace(tasks=[SimpleNamespace(executor="transfer")])
    notice = _build_policy_notice("send 10k to tolu and invest 10k", planner_output)

    assert notice is not None
    assert "money transfer" in notice
    assert "Investments" in notice
