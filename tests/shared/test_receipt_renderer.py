import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from apps.receipt.src.renderer import ReceiptRenderer
from shared.config.settings import settings


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
async def test_render_receipt_uses_fast_wait_and_local_font_html(monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = ReceiptRenderer()

    element = cast(Any, SimpleNamespace(screenshot=AsyncMock(return_value=b"png-bytes")))
    page = cast(
        Any,
        SimpleNamespace(
            set_default_timeout=Mock(),
            set_content=AsyncMock(),
            wait_for_selector=AsyncMock(),
            query_selector=AsyncMock(return_value=element),
            screenshot=AsyncMock(return_value=b"full-page"),
            close=AsyncMock(),
        ),
    )
    browser = cast(Any, SimpleNamespace(new_page=AsyncMock(return_value=page)))

    async def _get_browser() -> Any:
        return browser

    monkeypatch.setattr(renderer, "_get_browser", _get_browser)

    screenshot = await renderer.render_receipt(
        transfer_data={
            "amount": 1000,
            "recipient": {"name": "Jane", "account_number": "1234567890", "bank_name": "Opay"},
            "source": {"account_name": "John", "name": "First Bank", "account_number": "0987654321"},
            "channel": "whatsapp",
            "session_id": "SESSION-FAST-1",
            "processor_name": f"{settings.app_name} Gateway",
            "narration": "Test transfer",
        },
        transaction_reference="TRX-FAST-1",
        attempt=1,
    )

    assert screenshot == b"png-bytes"
    browser.new_page.assert_awaited_once_with(
        viewport={"width": 480, "height": 1},
        device_scale_factor=2,
    )
    page.set_content.assert_awaited_once()
    assert page.set_content.await_args is not None
    html_content = page.set_content.await_args.args[0]
    assert "fonts.googleapis.com" not in html_content
    assert "file://" in html_content
    assert "Transfer Successful" in html_content
    assert "Funds delivered to beneficiary bank" in html_content
    assert "WAT" in html_content
    assert "SESSION-FAST-1" in html_content
    assert "bottom-bar" not in html_content
    assert page.set_content.await_args.kwargs["wait_until"] == "domcontentloaded"
    page.wait_for_selector.assert_awaited_once_with("#receipt-container", state="visible", timeout=3000)
    element.screenshot.assert_awaited_once_with(type="png")
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_render_receipt_renders_safe_fallbacks_when_optional_fields_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = ReceiptRenderer()

    element = cast(Any, SimpleNamespace(screenshot=AsyncMock(return_value=b"png-bytes")))
    page = cast(
        Any,
        SimpleNamespace(
            set_default_timeout=Mock(),
            set_content=AsyncMock(),
            wait_for_selector=AsyncMock(),
            query_selector=AsyncMock(return_value=element),
            screenshot=AsyncMock(return_value=b"full-page"),
            close=AsyncMock(),
        ),
    )
    browser = cast(Any, SimpleNamespace(new_page=AsyncMock(return_value=page)))

    async def _get_browser() -> Any:
        return browser

    monkeypatch.setattr(renderer, "_get_browser", _get_browser)

    await renderer.render_receipt(
        transfer_data={"amount": 1000, "recipient": {}, "source": {}},
        transaction_reference="TRX-FALLBACK-1",
        attempt=1,
    )

    assert page.set_content.await_args is not None
    html_content = page.set_content.await_args.args[0]
    assert ">N/A<" in html_content


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


@pytest.mark.asyncio
async def test_render_receipt_serializes_concurrent_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = ReceiptRenderer()

    active_count = 0
    max_active_count = 0

    async def make_page() -> Any:
        nonlocal active_count, max_active_count

        async def set_content(*args: Any, **kwargs: Any) -> None:
            nonlocal active_count, max_active_count
            active_count += 1
            max_active_count = max(max_active_count, active_count)
            await asyncio.sleep(0.01)
            active_count -= 1

        element = cast(Any, SimpleNamespace(screenshot=AsyncMock(return_value=b"png-bytes")))
        return cast(
            Any,
            SimpleNamespace(
                set_default_timeout=Mock(),
                set_content=AsyncMock(side_effect=set_content),
                wait_for_selector=AsyncMock(),
                query_selector=AsyncMock(return_value=element),
                screenshot=AsyncMock(return_value=b"full-page"),
                close=AsyncMock(),
            ),
        )

    async def new_page(*args: Any, **kwargs: Any) -> Any:
        return await make_page()

    browser = cast(Any, SimpleNamespace(new_page=AsyncMock(side_effect=new_page)))

    async def _get_browser() -> Any:
        return browser

    monkeypatch.setattr(renderer, "_get_browser", _get_browser)

    await asyncio.gather(
        renderer.render_receipt(
            transfer_data={"recipient": {}, "source": {}, "amount": 1000},
            transaction_reference="TRX-CONC-1",
        ),
        renderer.render_receipt(
            transfer_data={"recipient": {}, "source": {}, "amount": 2000},
            transaction_reference="TRX-CONC-2",
        ),
    )

    assert max_active_count == 1
