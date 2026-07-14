import pytest

from apps.gateway import main as gateway_main
from apps.receipt import main as receipt_main
from apps.transaction import main as transaction_main
from shared.config.settings import Settings


def test_latency_related_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        "WHATSAPP_TYPING_INDICATOR_DELAY_MS",
        "TELEGRAM_TYPING_INDICATOR_DELAY_MS",
        "CHAT_WORKER_STREAM_BLOCK_MS",
        "TRANSACTION_WORKER_STREAM_BLOCK_MS",
        "RECEIPT_WORKER_STREAM_BLOCK_MS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    cfg = Settings()

    assert cfg.whatsapp.typing_indicator_delay_ms == 0
    assert cfg.telegram_typing_indicator_delay_ms == 0
    assert cfg.chat_worker_stream_block_ms == 5000
    assert cfg.transaction_worker_stream_block_ms == 5000
    assert cfg.receipt_worker_stream_block_ms == 5000


@pytest.mark.asyncio
async def test_gateway_readiness_reports_ingress_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_readiness(*, require_db: bool, require_redis: bool) -> dict[str, object]:
        assert require_db is True
        assert require_redis is True
        return {"status": "ready", "checks": {"db": {"status": "ready"}, "redis": {"status": "ready"}}}

    monkeypatch.setattr(gateway_main, "build_runtime_status", lambda runtime_name: {"runtime_name": runtime_name})
    monkeypatch.setattr(gateway_main, "dependency_readiness", fake_readiness)
    payload = await gateway_main.readiness()
    assert payload["service"] == "gateway"
    assert payload["status"] == "ready"
    assert payload["ingress_enabled"] is True
    assert payload["checks"]["db"]["status"] == "ready"
    assert payload["runtime"]["runtime_name"] == "gateway"


@pytest.mark.asyncio
async def test_receipt_readiness_reports_worker_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_readiness(*, require_db: bool, require_redis: bool) -> dict[str, object]:
        assert require_db is False
        assert require_redis is True
        return {"status": "ready", "checks": {"redis": {"status": "ready"}}}

    monkeypatch.setattr(receipt_main, "dependency_readiness", fake_readiness)
    payload = await receipt_main.readiness_check()
    assert payload["service"] == "receipt-worker"
    assert payload["status"] == "ready"
    assert payload["worker_enabled"] is True
    assert payload["async_transport"] == "redis"
    assert payload["checks"]["redis"]["status"] == "ready"


@pytest.mark.asyncio
async def test_transaction_worker_readiness_reports_domain_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_readiness(*, require_db: bool, require_redis: bool) -> dict[str, object]:
        assert require_db is True
        assert require_redis is True
        return {"status": "ready", "checks": {"db": {"status": "ready"}, "redis": {"status": "ready"}}}

    monkeypatch.setattr(transaction_main, "dependency_readiness", fake_readiness)
    transaction_main._loop_health.get("funding_reconciliation").mark_success()
    payload = await transaction_main.readiness_check()
    assert payload["service"] == "transaction-worker"
    assert payload["status"] == "ready"
    assert payload["enabled_domains"]["transaction"] is True
    assert payload["enabled_domains"]["funding"] is True
    assert payload["worker_enabled"] is True
    assert payload["async_transport"] == "redis"
    assert payload["checks"]["db"]["status"] == "ready"
    assert payload["loop_health"]["funding_reconciliation"]["success_count"] >= 1
