from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from shared.receipts.receipt_generator import ReceiptGenerator


def _sample_transaction() -> Any:
    return SimpleNamespace(
        amount=10000,
        currency="NGN",
        transaction_type="transfer",
        created_at=datetime(2026, 3, 5, 10, 0, 0),
        source_account_number="1234567890",
        recipient_name="Mum",
        recipient_account_number="8162511023",
        recipient_bank_name="Opay",
        recipient_bank_code="999",
        narration="Family support",
        transaction_id="TRX-LEG-1",
        status="successful",
    )


def test_generate_receipt_html_uses_local_fonts_only() -> None:
    generator = ReceiptGenerator()

    html = generator.generate_receipt_html(_sample_transaction())

    assert "fonts.googleapis.com" not in html
    assert "file://" in html
    assert "InterReceipt" in html


@pytest.mark.asyncio
async def test_html_to_image_uses_fast_wait_and_lower_device_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = ReceiptGenerator()

    element = cast(Any, SimpleNamespace(screenshot=AsyncMock(return_value=b"png-bytes")))
    page = cast(
        Any,
        SimpleNamespace(
            set_default_timeout=Mock(),
            set_viewport_size=AsyncMock(),
            set_content=AsyncMock(),
            wait_for_selector=AsyncMock(),
            query_selector=AsyncMock(return_value=element),
            screenshot=AsyncMock(return_value=b"page-png"),
            close=AsyncMock(),
        ),
    )
    browser = cast(Any, SimpleNamespace(new_page=AsyncMock(return_value=page)))

    async def _ensure_browser() -> Any:
        return browser

    monkeypatch.setattr(generator, "_ensure_browser", _ensure_browser)

    png = await generator.html_to_image("<html><body><div id='receipt-container'></div></body></html>", reference="TRX-1")

    assert png == b"png-bytes"
    browser.new_page.assert_awaited_once_with(
        viewport={"width": 800, "height": 1200},
        device_scale_factor=2,
    )
    page.set_content.assert_awaited_once_with(
        "<html><body><div id='receipt-container'></div></body></html>",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector.assert_awaited_once_with("#receipt-container", state="visible", timeout=3000)
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_html_to_image_resets_runtime_on_browser_closed_error() -> None:
    generator = ReceiptGenerator()

    broken_browser = cast(
        Any,
        SimpleNamespace(
            is_connected=lambda: True,
            new_page=AsyncMock(side_effect=RuntimeError("Target page, context or browser has been closed")),
            close=AsyncMock(),
        ),
    )
    playwright_runtime = cast(Any, SimpleNamespace(stop=AsyncMock()))
    generator._browser = broken_browser
    generator._playwright = playwright_runtime

    with pytest.raises(RuntimeError, match="Target page, context or browser has been closed"):
        await generator.html_to_image("<html></html>", reference="TRX-2")

    broken_browser.close.assert_awaited_once()
    playwright_runtime.stop.assert_awaited_once()
    assert generator._browser is None
    assert generator._playwright is None
