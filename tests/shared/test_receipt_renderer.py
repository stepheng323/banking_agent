from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from apps.receipt.src.renderer import ReceiptRenderer


@pytest.mark.asyncio
async def test_get_browser_relaunches_when_cached_browser_is_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = ReceiptRenderer()

    stale_browser = cast(Any, SimpleNamespace(is_connected=lambda: False, close=AsyncMock()))
    stale_playwright = cast(Any, SimpleNamespace(stop=AsyncMock()))
    fresh_browser = cast(Any, SimpleNamespace(is_connected=lambda: True))
    launch = AsyncMock(return_value=fresh_browser)
    fresh_playwright = cast(
        Any,
        SimpleNamespace(
            chromium=SimpleNamespace(launch=launch),
            stop=AsyncMock(),
        ),
    )
    start = AsyncMock(return_value=fresh_playwright)

    renderer._browser = stale_browser
    renderer._playwright = stale_playwright

    monkeypatch.setattr(
        "apps.receipt.src.renderer.async_playwright",
        lambda: SimpleNamespace(start=start),
    )

    browser = await renderer._get_browser()

    assert browser is fresh_browser
    stale_browser.close.assert_awaited_once()
    stale_playwright.stop.assert_awaited_once()
    launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_render_receipt_resets_runtime_when_new_page_fails() -> None:
    renderer = ReceiptRenderer()

    broken_browser = cast(
        Any,
        SimpleNamespace(
            is_connected=lambda: True,
            new_page=AsyncMock(side_effect=RuntimeError("Target page, context or browser has been closed")),
            close=AsyncMock(),
        ),
    )
    playwright_runtime = cast(Any, SimpleNamespace(stop=AsyncMock()))
    renderer._browser = broken_browser
    renderer._playwright = playwright_runtime

    with pytest.raises(RuntimeError, match="Target page, context or browser has been closed"):
        await renderer.render_receipt(
            transfer_data={
                "amount": 1000,
                "recipient": {"name": "Jane", "account_number": "1234567890", "bank_name": "Opay"},
                "source": {"account_name": "John"},
                "narration": "Test transfer",
            },
            transaction_reference="TRX-FAIL-1",
        )

    broken_browser.close.assert_awaited_once()
    playwright_runtime.stop.assert_awaited_once()
    assert renderer._browser is None
    assert renderer._playwright is None
