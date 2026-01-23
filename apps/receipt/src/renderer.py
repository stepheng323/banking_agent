"""HTML receipt renderer using Playwright."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
from playwright.async_api import Browser, Playwright, async_playwright

from shared.utils.logging import get_logger

logger = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
RECEIPT_WIDTH = 480


class ReceiptRenderer:
    """Renders HTML receipts to PNG images using Playwright."""

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._playwright: Playwright | None = None
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )

    async def _get_browser(self) -> Browser:
        """Get or create browser instance."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            logger.info("playwright_browser_launched")
        return self._browser

    async def close(self) -> None:
        """Close browser and cleanup."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("playwright_browser_closed")

    async def render_receipt(
        self,
        transfer_data: dict[str, Any],
        transaction_reference: str,
    ) -> bytes:
        """Render receipt HTML to PNG image bytes."""
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
        }

        template = self._jinja_env.get_template("receipt.html")
        html_content = template.render(**template_data)

        browser = await self._get_browser()
        page = await browser.new_page(
            viewport={"width": RECEIPT_WIDTH, "height": 1},
            device_scale_factor=3,
        )

        try:
            await page.set_content(html_content)
            await page.wait_for_load_state("networkidle")

            element = await page.query_selector("#receipt-container")
            if element:
                screenshot = await element.screenshot(type="png")
            else:
                screenshot = await page.screenshot(type="png", full_page=True)

            logger.info("receipt_rendered", size_bytes=len(screenshot))
            return screenshot

        finally:
            await page.close()

    def _format_amount(self, amount: float | Decimal) -> str:
        """Format amount with thousand separators."""
        return f"{float(amount):,.0f}"

    def _mask_account(self, account_number: str) -> str:
        """Mask account number showing only last 4 digits."""
        if len(account_number) >= 4:
            return f"**** {account_number[-4:]}"
        return account_number
