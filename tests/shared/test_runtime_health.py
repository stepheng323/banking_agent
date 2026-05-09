import pytest

from apps.gateway import main as gateway_main
from apps.receipt import main as receipt_main
from apps.transaction import main as transaction_main


@pytest.mark.asyncio
async def test_gateway_readiness_reports_ingress_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_main, "build_runtime_status", lambda runtime_name: {"runtime_name": runtime_name})
    payload = await gateway_main.readiness()
    assert payload["service"] == "gateway"
    assert payload["ingress_enabled"] is True
    assert payload["runtime"]["runtime_name"] == "gateway"


@pytest.mark.asyncio
async def test_receipt_readiness_reports_worker_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_main.settings, "async_transport", "redis")
    payload = await receipt_main.readiness_check()
    assert payload["service"] == "receipt-worker"
    assert payload["worker_enabled"] is True


@pytest.mark.asyncio
async def test_transaction_worker_readiness_reports_domain_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transaction_main.settings, "async_transport", "redis")
    payload = await transaction_main.readiness_check()
    assert payload["service"] == "transaction-worker"
    assert payload["enabled_domains"]["transaction"] is True
    assert payload["enabled_domains"]["funding"] is True
    assert payload["worker_enabled"] is True
