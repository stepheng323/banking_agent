"""HTML receipt renderer using Playwright."""

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader
from playwright.async_api import Browser, Playwright, async_playwright

from shared.utils.logging import get_logger

logger = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FONTS_DIR = PROJECT_ROOT / "shared" / "receipts" / "fonts"
RECEIPT_WIDTH = 480
RENDER_WAIT_UNTIL: Literal["domcontentloaded"] = "domcontentloaded"
RENDER_DEVICE_SCALE_FACTOR = 2
RENDER_SELECTOR_TIMEOUT_MS = 3000
RENDER_PAGE_TIMEOUT_MS = 8000
CHROMIUM_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--no-zygote",
    "--single-process",
    "--disable-gpu",
]


class ReceiptBrowserRuntimeClosedError(RuntimeError):
    """Typed runtime failure raised when Playwright page/context/browser is closed."""


def is_browser_runtime_closed_error(error: Exception | str) -> bool:
    """Return True when error indicates browser/page/context runtime has been closed."""
    error_text = error if isinstance(error, str) else str(error)
    normalized = error_text.lower()
    return any(
        marker in normalized
        for marker in (
            "target page, context or browser has been closed",
            "browser has been closed",
            "context has been closed",
            "page has been closed",
        )
    )


class ReceiptRenderer:
    """Renders HTML receipts to PNG images using Playwright."""

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._playwright: Playwright | None = None
        self._runtime_lock = asyncio.Lock()
        self._render_lock = asyncio.Lock()
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )

    async def _get_browser(self) -> Browser:
        """Get or create browser instance."""
        async with self._runtime_lock:
            return await self._get_browser_locked()

    async def _get_browser_locked(self) -> Browser:
        """Get or create browser instance under lifecycle lock."""
        if self._browser is not None and not self._browser.is_connected():
            logger.warning("playwright_browser_disconnected_reinitializing")
            await self._reset_browser_runtime_locked()

        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=CHROMIUM_LAUNCH_ARGS,
                chromium_sandbox=False,
            )
            logger.info("playwright_browser_launched")
        return self._browser

    async def _reset_browser_runtime(self) -> None:
        """Clear cached browser/runtime so next request can relaunch cleanly."""
        async with self._runtime_lock:
            await self._reset_browser_runtime_locked()

    async def _reset_browser_runtime_locked(self) -> None:
        """Clear cached browser/runtime so next request can relaunch cleanly."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning("playwright_browser_close_failed", error=str(e))
            finally:
                self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning("playwright_runtime_stop_failed", error=str(e))
            finally:
                self._playwright = None

    async def close(self) -> None:
        """Close browser and cleanup."""
        await self._reset_browser_runtime()
        logger.info("playwright_browser_closed")

    async def render_receipt(
        self,
        transfer_data: dict[str, Any],
        transaction_reference: str,
        attempt: int | None = None,
    ) -> bytes:
        """Render receipt HTML to PNG image bytes."""
        async with self._render_lock:
            render_started_at = asyncio.get_running_loop().time()
            logger.info(
                "receipt_render_started",
                reference=transaction_reference,
                attempt=attempt,
            )

            recipient = transfer_data.get("recipient", {})
            source = transfer_data.get("source", {})
            amount = transfer_data.get("amount", 0)

            sender_name = source.get("account_name") or source.get("name", "Unknown")
            recipient_name = recipient.get("name", "Unknown")

            template_data = {
                "status": "Successful",
                "amount": self._format_amount(amount),
                "amount_decimal": ".00",
                "currency_symbol": "₦",
                "date_time": datetime.now().strftime("%b %d, %Y at %I:%M %p"),
                "sender_name": sender_name,
                "recipient_name": recipient_name,
                "recipient_bank": recipient.get("bank_name", ""),
                "recipient_account_masked": self._mask_account(recipient.get("account_number", "")),
                "transaction_reference": transaction_reference,
                "narration": transfer_data.get("narration", ""),
                "inter_regular_font_url": self._font_file_url("Inter-Regular.ttf"),
                "inter_medium_font_url": self._font_file_url("Inter-Medium.ttf"),
                "inter_semibold_font_url": self._font_file_url("Inter-SemiBold.ttf"),
                "inter_bold_font_url": self._font_file_url("Inter-Bold.ttf"),
            }

            template = self._jinja_env.get_template("receipt.html")
            html_content = template.render(**template_data)

            browser = await self._get_browser()
            try:
                page = await browser.new_page(
                    viewport={"width": RECEIPT_WIDTH, "height": 1},
                    device_scale_factor=RENDER_DEVICE_SCALE_FACTOR,
                )
            except Exception as e:
                logger.warning("playwright_new_page_failed", error=str(e))
                browser_closed_error = is_browser_runtime_closed_error(e)
                if browser_closed_error:
                    await self._reset_browser_runtime()
                logger.error(
                    "receipt_render_failed",
                    reference=transaction_reference,
                    attempt=attempt,
                    error=str(e),
                    error_class="browser_closed" if browser_closed_error else "other",
                )
                if browser_closed_error:
                    raise ReceiptBrowserRuntimeClosedError(str(e)) from e
                raise

            set_content_ms: float | None = None
            screenshot_ms: float | None = None
            try:
                page.set_default_timeout(RENDER_PAGE_TIMEOUT_MS)
                set_content_started = asyncio.get_running_loop().time()
                await page.set_content(html_content, wait_until=RENDER_WAIT_UNTIL)
                await page.wait_for_selector("#receipt-container", state="visible", timeout=RENDER_SELECTOR_TIMEOUT_MS)
                set_content_ms = (asyncio.get_running_loop().time() - set_content_started) * 1000

                screenshot_started = asyncio.get_running_loop().time()
                element = await page.query_selector("#receipt-container")
                if element:
                    screenshot = await element.screenshot(type="png")
                else:
                    logger.warning("receipt_selector_not_found_fallback_full_page", reference=transaction_reference)
                    screenshot = await page.screenshot(type="png", full_page=True)
                screenshot_ms = (asyncio.get_running_loop().time() - screenshot_started) * 1000

                render_ms = (asyncio.get_running_loop().time() - render_started_at) * 1000
                logger.info(
                    "receipt_render_completed",
                    reference=transaction_reference,
                    attempt=attempt,
                    render_ms=round(render_ms, 2),
                    set_content_ms=round(set_content_ms, 2),
                    screenshot_ms=round(screenshot_ms, 2),
                    size_bytes=len(screenshot),
                )
                return screenshot
            except Exception as e:
                browser_closed_error = is_browser_runtime_closed_error(e)
                render_ms = (asyncio.get_running_loop().time() - render_started_at) * 1000
                logger.error(
                    "receipt_render_failed",
                    reference=transaction_reference,
                    attempt=attempt,
                    render_ms=round(render_ms, 2),
                    set_content_ms=round(set_content_ms, 2) if set_content_ms is not None else None,
                    screenshot_ms=round(screenshot_ms, 2) if screenshot_ms is not None else None,
                    error=str(e),
                    error_class="browser_closed" if browser_closed_error else "other",
                )
                if browser_closed_error:
                    await self._reset_browser_runtime()
                    raise ReceiptBrowserRuntimeClosedError(str(e)) from e
                raise
            finally:
                await page.close()

    def _format_amount(self, amount: float | Decimal) -> str:
        """Format amount with thousand separators."""
        return f"{float(amount):,.0f}"

    def _font_file_url(self, file_name: str) -> str:
        return FONTS_DIR.joinpath(file_name).resolve().as_uri()

    def _mask_account(self, account_number: str) -> str:
        """Mask account number showing only last 4 digits."""
        if len(account_number) >= 4:
            return f"**** {account_number[-4:]}"
        return account_number
