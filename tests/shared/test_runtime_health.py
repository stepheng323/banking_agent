import pytest

from apps.core.src import main as core_main
from apps.core.src import transaction_worker_app
from apps.gateway import main as gateway_main
from apps.receipt import main as receipt_main


@pytest.mark.asyncio
async def test_core_health_exposes_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_main, "build_runtime_status", lambda runtime_name: {"runtime_name": runtime_name, "stack_role": "vps-standby"})
    payload = await core_main.health()
    assert payload["service"] == "core-api"
    assert payload["stack_role"] == "vps-standby"


@pytest.mark.asyncio
async def test_gateway_readiness_reports_ingress_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_main.settings, "enable_webhook_ingress", False)
    payload = await gateway_main.readiness()
    assert payload["service"] == "gateway"
    assert payload["ingress_enabled"] is False


@pytest.mark.asyncio
async def test_receipt_readiness_reports_worker_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_main.settings, "enable_receipt_worker", True)
    payload = await receipt_main.readiness_check()
    assert payload["service"] == "receipt-worker"
    assert payload["worker_enabled"] is True


@pytest.mark.asyncio
async def test_transaction_worker_readiness_reports_domain_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transaction_worker_app.settings, "enable_transaction_worker", True)
    monkeypatch.setattr(transaction_worker_app.settings, "enable_funding_worker", False)
    monkeypatch.setattr(transaction_worker_app.settings, "enable_payout_worker", False)
    monkeypatch.setattr(transaction_worker_app.settings, "enable_refund_worker", False)
    payload = await transaction_worker_app.readiness_check()
    assert payload["service"] == "transaction-worker"
    assert payload["enabled_domains"]["transaction"] is True
    assert payload["enabled_domains"]["funding"] is False
