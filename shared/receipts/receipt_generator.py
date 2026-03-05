"""Receipt generator service for creating image receipts from HTML template."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Browser, Playwright, async_playwright

from shared.database.models import Transaction
from shared.utils.logging import get_logger

logger = get_logger(__name__)

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
RENDER_WAIT_UNTIL = "domcontentloaded"
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


RECEIPT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang='en'>
  <head>
    <meta charset='UTF-8' />
    <meta name='viewport' content='width=device-width, initial-scale=1.0' />
    <title>Transaction Receipt - Fusepay</title>
    <style>
      @font-face {
        font-family: 'InterReceipt';
        src: url('{{interRegularFontUrl}}') format('truetype');
        font-weight: 400;
        font-style: normal;
        font-display: swap;
      }

      @font-face {
        font-family: 'InterReceipt';
        src: url('{{interSemiBoldFontUrl}}') format('truetype');
        font-weight: 600;
        font-style: normal;
        font-display: swap;
      }

      @font-face {
        font-family: 'InterReceipt';
        src: url('{{interBoldFontUrl}}') format('truetype');
        font-weight: 700;
        font-style: normal;
        font-display: swap;
      }

      * { box-sizing: border-box; }
      body {
        font-family: 'InterReceipt', Arial, sans-serif;
        background: #ffffff;
        margin: 0;
        padding: 0;
        line-height: 1.8;
        color: #333;
        font-weight: 700;
        display: flex;
        justify-content: center;
      }

      .receipt {
        max-width: 650px;
        margin: 16px auto;
        padding: 0;
        background: #fff;
      }

      .header {
        background: #ffffff;
        padding: 16px 40px 10px;
        border-bottom: 1px solid #eee;
        position: relative;
      }

      .logo-section {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 30px;
      }

      .logo {
        display: flex;
        align-items: center;
        gap: 50px;
      }

      .logo-icon {
        width: 40px;
        height: 40px;
        background: #8b5cf6;
        position: relative;
        transform: rotate(45deg);
      }

      .logo-icon::before {
        content: '';
        position: absolute;
        top: 8px;
        left: 8px;
        right: 8px;
        bottom: 8px;
        border: 2px solid white;
        transform: rotate(-45deg);
      }

      .logo-text {
        font-size: 34px;
        font-weight: 700;
        color: #1e4a72;
        letter-spacing: -0.5px;
      }

      .tagline {
        color: #8b5cf6;
        font-size: 16px;
        font-weight: 600;
        position: relative;
      }

      .tagline::before {
        content: '';
        position: absolute;
        left: -50px;
        top: 50%;
        width: 40px;
        height: 2px;
        background: #8b5cf6;
        transform: translateY(-50%) rotate(-45deg);
      }

      .receipt-title {
        text-align: center;
        font-size: 30px;
        font-weight: 600;
        color: #1e4a72;
        margin: 24px 0 18px;
      }

      .generated-info {
        text-align: center;
        color: #888;
        font-size: 18px;
        margin-bottom: 24px;
      }

      .generated-info strong {
        color: #666;
      }

      .content {
        padding: 16px 40px 6px;
        font-size: 18px;
      }

      .transaction-row {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        padding: 8px 0;
        border-bottom: 1px solid #f0f0f0;
        gap: 56px;
      }

      .transaction-row:last-of-type {
        border-bottom: 2px solid #eee;
        margin-bottom: 10px;
      }

      .label {
        font-weight: 600;
        color: #8b5cf6;
        font-size: 18px;
        width: 200px;
        flex-shrink: 0;
        white-space: nowrap;
      }

      .value {
        color: #1e4a72;
        font-size: 18px;
        font-weight: 500;
        text-align: left;
        flex: 1;
        word-break: break-word;
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        gap: 0;
      }

      .amount-value {
        font-size: 24px;
        font-weight: 700;
      }

      .status-successful {
        color: #2d5a27;
        font-weight: 600;
      }

      .footer {
        background: #f8f9fa;
        padding: 10px 40px 6px;
        border-top: 1px solid #eee;
        font-size: 16px;
        line-height: 1.8;
        color: #666;
      }

      .contact-info {
        margin-bottom: 15px;
      }

      .contact-links {
        color: #1e4a72;
        text-decoration: none;
        font-weight: 500;
      }

      .thank-you {
        margin: 15px 0;
        color: #333;
      }

      .banking-options {
        color: #888;
        font-size: 12px;
        margin-top: 15px;
      }

      @media print {
        body {
          background: none;
          padding: 0;
        }

        .receipt {
          margin: 0;
        }
      }
    </style>
  </head>
  <body style='background: #fff; margin: 0; padding: 0;'>
    <div class='receipt' id='receipt-container'>
      <div class='header'>
        <div class='logo-section'>
          <div class='logo'>
            <div class='logo-icon'></div>
            <div class='logo-text'>Fusepay</div>
          </div>
          <div class='tagline'>your money, just a wish away</div>
        </div>

        <div class='receipt-title'>Transaction Receipt</div>
        <div class='generated-info'>
          Generated from
          <strong>Fusepay</strong>
          on
          {{generationDate}}
        </div>
      </div>
      <div class='content'>
        <div class='transaction-row'>
          <div class='label'>Transaction Amount</div>
          <div class='value amount-value'>{{amount}}</div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Transaction Type</div>
          <div class='value'>{{transactionType}}</div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Transaction Date</div>
          <div class='value'>{{transactionDate}}</div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Sender</div>
          <div class='value'>{{sourceAccountName}}</div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Beneficiary</div>
          <div class='value'>
            {{beneficiaryName}}<br />
            {{beneficiaryAccount}}<br />
            {{beneficiaryBank}}
          </div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Remark</div>
          <div class='value'>{{narration}}</div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Transaction Reference</div>
          <div class='value'>{{reference}}</div>
        </div>

        <div class='transaction-row'>
          <div class='label'>Transaction Status</div>
          <div class='value status-successful'>{{status}}</div>
        </div>
      </div>

      <div class='footer'>
        <div class='contact-info'>
          If you have any questions or would like more information, please call
          our 24-hour Contact Centre on
          <a href='tel:0700CALLPAYGENIE' class='contact-links'>0700CALLPAYGENIE</a>,
          <a href='tel:07003000000' class='contact-links'>0700 3000000</a>,
          <a href='tel:+2342012712005' class='contact-links'>+234 201-2712005-7</a>,
          <a href='tel:+2342012802500' class='contact-links'>+234 201-2802500</a>
          or send an email to
          <a href='mailto:support@paygenie.ai' class='contact-links'>support@paygenie.ai</a>
        </div>

        <div class='thank-you'>
          Thank you for choosing Fusepay.
        </div>

        <div class='banking-options'>
          Banking with Fusepay: Branch | ATM | Online | Mobile | Contact centre
        </div>
      </div>
    </div>
  </body>
</html>"""


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


class ReceiptGenerator:
    """Service for generating receipt images from HTML template."""

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._playwright: Playwright | None = None
        self._runtime_lock = asyncio.Lock()
        self._render_lock = asyncio.Lock()

    async def _ensure_browser(self) -> Browser:
        """Ensure browser is initialized."""
        async with self._runtime_lock:
            if self._browser is not None and not self._browser.is_connected():
                logger.warning("legacy_receipt_browser_disconnected_reinitializing")
                await self._reset_browser_runtime_locked()

            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=CHROMIUM_LAUNCH_ARGS,
                    chromium_sandbox=False,
                )
                logger.info("legacy_receipt_browser_launched")
            return self._browser

    async def _reset_browser_runtime(self) -> None:
        """Reset browser runtime under lock."""
        async with self._runtime_lock:
            await self._reset_browser_runtime_locked()

    async def _reset_browser_runtime_locked(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception as error:  # pragma: no cover - defensive close path
                logger.warning("legacy_receipt_browser_close_failed", error=str(error))
            finally:
                self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as error:  # pragma: no cover - defensive stop path
                logger.warning("legacy_receipt_playwright_stop_failed", error=str(error))
            finally:
                self._playwright = None

    async def close(self) -> None:
        """Close browser and cleanup resources."""
        await self._reset_browser_runtime()

    def _font_file_url(self, file_name: str) -> str:
        return FONTS_DIR.joinpath(file_name).resolve().as_uri()

    def generate_receipt_html(self, transaction: Transaction, account_name: str | None = None) -> str:
        """
        Generate receipt HTML from template with transaction data.

        Args:
            transaction: Transaction database model instance
            account_name: Source account name from Account model

        Returns:
            Rendered HTML string
        """
        amount = transaction.amount
        currency = transaction.currency or "NGN"
        amount_str = f"₦{amount:,.2f}" if currency == "NGN" else f"{currency} {amount:,.2f}"

        transaction_type = transaction.transaction_type or "Transfer"
        transaction_type = transaction_type.capitalize()
        created_at = transaction.created_at
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                created_at = datetime.now(UTC)
        transaction_date = created_at.strftime("%d %b %Y") if created_at else "N/A"
        source_account_name = account_name or transaction.source_account_number or "N/A"
        beneficiary_name = transaction.recipient_name or "N/A"
        beneficiary_account = transaction.recipient_account_number or "N/A"
        beneficiary_bank = transaction.recipient_bank_name or transaction.recipient_bank_code or "N/A"

        narration = transaction.narration or "No narration"
        reference = transaction.transaction_id or "Pending"
        status = transaction.status.capitalize() if transaction.status else "Pending"
        generation_date = datetime.now(UTC).strftime("%d %b %Y at %I:%M %p")

        html = RECEIPT_HTML_TEMPLATE.replace("{{amount}}", amount_str)
        html = html.replace("{{transactionType}}", transaction_type)
        html = html.replace("{{transactionDate}}", transaction_date)
        html = html.replace("{{sourceAccountName}}", source_account_name)
        html = html.replace("{{beneficiaryName}}", beneficiary_name)
        html = html.replace("{{beneficiaryAccount}}", beneficiary_account)
        html = html.replace("{{beneficiaryBank}}", beneficiary_bank)
        html = html.replace("{{narration}}", narration)
        html = html.replace("{{reference}}", reference)
        html = html.replace("{{status}}", status)
        html = html.replace("{{generationDate}}", generation_date)
        html = html.replace("{{interRegularFontUrl}}", self._font_file_url("Inter-Regular.ttf"))
        html = html.replace("{{interSemiBoldFontUrl}}", self._font_file_url("Inter-SemiBold.ttf"))
        html = html.replace("{{interBoldFontUrl}}", self._font_file_url("Inter-Bold.ttf"))

        return html

    async def html_to_image(self, html_content: str, reference: str | None = None) -> bytes:
        """
        Convert HTML content to PNG image bytes.

        Args:
            html_content: HTML string to render
            reference: Optional transaction reference for observability

        Returns:
            PNG image bytes
        """
        async with self._render_lock:
            render_started_at = asyncio.get_running_loop().time()
            logger.info("legacy_receipt_render_started", reference=reference)
            browser = await self._ensure_browser()
            try:
                page = await browser.new_page(
                    viewport={"width": 800, "height": 1200},
                    device_scale_factor=RENDER_DEVICE_SCALE_FACTOR,
                )
            except Exception as error:
                logger.warning("legacy_receipt_new_page_failed", error=str(error), reference=reference)
                if is_browser_runtime_closed_error(error):
                    await self._reset_browser_runtime()
                raise

            set_content_ms: float | None = None
            screenshot_ms: float | None = None
            try:
                page.set_default_timeout(RENDER_PAGE_TIMEOUT_MS)
                set_content_started = asyncio.get_running_loop().time()
                await page.set_viewport_size({"width": 800, "height": 1200})
                await page.set_content(html_content, wait_until=RENDER_WAIT_UNTIL)
                await page.wait_for_selector("#receipt-container", state="visible", timeout=RENDER_SELECTOR_TIMEOUT_MS)
                set_content_ms = (asyncio.get_running_loop().time() - set_content_started) * 1000

                screenshot_started = asyncio.get_running_loop().time()
                container = await page.query_selector("#receipt-container")
                if container:
                    screenshot_bytes = await container.screenshot(type="png")
                else:
                    logger.warning("legacy_receipt_selector_not_found_fallback_full_page", reference=reference)
                    screenshot_bytes = await page.screenshot(type="png", full_page=True, clip=None)
                screenshot_ms = (asyncio.get_running_loop().time() - screenshot_started) * 1000

                render_ms = (asyncio.get_running_loop().time() - render_started_at) * 1000
                logger.info(
                    "legacy_receipt_render_completed",
                    reference=reference,
                    render_ms=round(render_ms, 2),
                    set_content_ms=round(set_content_ms, 2),
                    screenshot_ms=round(screenshot_ms, 2),
                    size_bytes=len(screenshot_bytes),
                )
                return screenshot_bytes
            except Exception as error:
                render_ms = (asyncio.get_running_loop().time() - render_started_at) * 1000
                logger.error(
                    "legacy_receipt_render_failed",
                    reference=reference,
                    render_ms=round(render_ms, 2),
                    set_content_ms=round(set_content_ms, 2) if set_content_ms is not None else None,
                    screenshot_ms=round(screenshot_ms, 2) if screenshot_ms is not None else None,
                    error=str(error),
                    error_class="browser_closed" if is_browser_runtime_closed_error(error) else "other",
                )
                if is_browser_runtime_closed_error(error):
                    await self._reset_browser_runtime()
                raise
            finally:
                await page.close()

    async def generate_receipt_image(self, transaction: Transaction, account_name: str | None = None) -> bytes:
        """
        Generate receipt image from transaction data.

        Args:
            transaction: Transaction database model instance
            account_name: Source account name from Account model

        Returns:
            PNG image bytes
        """
        html = self.generate_receipt_html(transaction, account_name)
        return await self.html_to_image(html, reference=transaction.transaction_id)
