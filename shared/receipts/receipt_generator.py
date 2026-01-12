"""Receipt generator service for creating image receipts from HTML template."""

from datetime import datetime

from playwright.async_api import Browser, async_playwright

from shared.database.models import Transaction

RECEIPT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang='en'>
  <head>
    <meta charset='UTF-8' />
    <meta name='viewport' content='width=device-width, initial-scale=1.0' />
    <title>Transaction Receipt - Fusepay</title>
    <link
      href='https://fonts.googleapis.com/css?family=Poppins:400,600,700&display=swap'
      rel='stylesheet'
    />
    <style>
      * { box-sizing: border-box; } body { font-family: 'Poppins', Arial,
      sans-serif; background: #ffffff; margin: 0; padding: 0; line-height: 1.8;
      color: #333; font-weight: 700; display: flex; justify-content: center; }
      .receipt { max-width: 650px; margin: 16px auto; padding: 0; background:
      #fff; box-sizing: border-box; } .header { background: #ffffff; padding:
      16px 40px 10px; border-bottom: 1px solid #eee; position: relative; }
      .logo-section { display: flex; align-items: center; justify-content:
      space-between; margin-bottom: 30px; } .logo { display: flex; align-items:
      center; gap: 50px; } .logo-icon { width: 40px; height: 40px; background:
      #8B5CF6; position: relative; transform: rotate(45deg); }
      .logo-icon::before { content: ''; position: absolute; top: 8px; left: 8px;
      right: 8px; bottom: 8px; border: 2px solid white; transform:
      rotate(-45deg); } .logo-text { font-size: 34px; font-weight: 700; color:
      #1e4a72; letter-spacing: -0.5px; } .tagline { color: #8B5CF6; font-size:
      16px; font-weight: 600; position: relative; } .tagline::before { content:
      ''; position: absolute; left: -50px; top: 50%; width: 40px; height: 2px;
      background: #8B5CF6; transform: translateY(-50%) rotate(-45deg); }
      .receipt-title { text-align: center; font-size: 30px; font-weight: 600;
      color: #1e4a72; margin: 24px 0 18px; } .generated-info { text-align:
      center; color: #888; font-size: 18px; margin-bottom: 24px; }
      .generated-info strong { color: #666; } .content { padding: 16px 40px 6px;
      font-size: 18px; } .transaction-row { display: flex; justify-content:
      space-between; align-items: flex-start; padding: 8px 0; border-bottom: 1px
      solid #f0f0f0; gap: 56px; } .transaction-row:last-of-type { border-bottom:
      2px solid #eee; margin-bottom: 10px; } .label { font-weight: 600; color:
      #8B5CF6; font-size: 18px; width: 200px; flex-shrink: 0; white-space:
      nowrap; } .value { color: #1e4a72; font-size: 18px; font-weight: 500;
      text-align: left; flex: 1; word-break: break-word; display: flex;
      flex-direction: column; align-items: flex-start; gap: 0; } .amount-value {
      font-size: 24px; font-weight: 700; } .status-successful { color: #2d5a27;
      font-weight: 600; } .footer { background: #f8f9fa; padding: 10px 40px 6px;
      border-top: 1px solid #eee; font-size: 16px; line-height: 1.8; color:
      #666; } .contact-info { margin-bottom: 15px; } .contact-links { color:
      #1e4a72; text-decoration: none; font-weight: 500; } .thank-you { margin:
      15px 0; color: #333; } .banking-options { color: #888; font-size: 12px;
      margin-top: 15px; } @media print { body { background: none; padding: 0; }
      .receipt { margin: 0; } }
    </style>
  </head>
  <body style='background: #fff; margin: 0; padding: 0;'>
    <div class='receipt'>
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
          <a
            href='tel:0700CALLPAYGENIE'
            class='contact-links'
          >0700CALLPAYGENIE</a>,
          <a href='tel:07003000000' class='contact-links'>0700 3000000</a>,
          <a href='tel:+2342012712005' class='contact-links'>+234 201-2712005-7</a>,
          <a href='tel:+2342012802500' class='contact-links'>+234 201-2802500</a>
          or send an email to
          <a
            href='mailto:support@paygenie.ai'
            class='contact-links'
          >support@paygenie.ai</a>
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


class ReceiptGenerator:
    """Service for generating receipt images from HTML template."""

    def __init__(self):
        self._browser: Browser | None = None
        self._playwright = None

    async def _ensure_browser(self) -> Browser:
        """Ensure browser is initialized."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser

    async def close(self):
        """Close browser and cleanup resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

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
                created_at = datetime.utcnow()
        transaction_date = created_at.strftime("%d %b %Y") if created_at else "N/A"
        source_account_name = account_name or transaction.source_account_number or "N/A"
        beneficiary_name = transaction.recipient_name or "N/A"
        beneficiary_account = transaction.recipient_account_number or "N/A"
        beneficiary_bank = transaction.recipient_bank_name or transaction.recipient_bank_code or "N/A"

        narration = transaction.narration or "No narration"
        reference = transaction.transaction_id or "Pending"
        status = transaction.status.capitalize() if transaction.status else "Pending"
        generation_date = datetime.utcnow().strftime("%d %b %Y at %I:%M %p")

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

        return html

    async def html_to_image(self, html_content: str) -> bytes:
        """
        Convert HTML content to PNG image bytes.

        Args:
            html_content: HTML string to render

        Returns:
            PNG image bytes
        """
        browser = await self._ensure_browser()
        page = await browser.new_page(
            viewport={"width": 800, "height": 1200},
            device_scale_factor=4,
        )

        try:
            await page.set_viewport_size({"width": 800, "height": 1200})
            await page.set_content(html_content, wait_until="networkidle")

            screenshot_bytes = await page.screenshot(
                type="png",
                full_page=True,
                clip=None,
            )

            return screenshot_bytes
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
        image_bytes = await self.html_to_image(html)
        return image_bytes
