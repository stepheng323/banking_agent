"""Premium image receipt generator using Pillow.

Follows the "Stripe-meets-Apple" aesthetic:
- Extreme restraint and functional typography
- Large amount as visual anchor
- Generous negative space (80px+ margins)
- Muted success indicators
- Inter font family for premium feel
"""

import textwrap
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from shared.receipts.models import TransferReceiptData

FONTS_DIR = Path(__file__).parent / "fonts"

BG_COLOR = "#FFFFFF"
TEXT_PRIMARY = "#111827"
TEXT_SECONDARY = "#6B7280"
SUCCESS_GREEN = "#059669"


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font from the fonts directory."""
    font_path = FONTS_DIR / f"Inter-{name}.ttf"
    try:
        return ImageFont.truetype(str(font_path), size)
    except OSError:
        return ImageFont.load_default()


def _format_amount(amount) -> str:
    """Format amount with Naira symbol and comma separators."""
    try:
        value = float(amount)
        return f"₦{value:,.2f}"
    except (ValueError, TypeError):
        return f"₦{amount}"


def generate_transfer_receipt_image(data: TransferReceiptData) -> bytes:
    """Generate a premium transfer receipt image with polished design."""

    WIDTH, HEIGHT = 840, 1350
    MARGIN_X = 80

    SECTION_SPACING = 35
    ROW_SPACING = 80
    ROW_WITH_SUB_SPACING = 110
    DIVIDER_WIDTH = 2

    COLORS = {
        "bg_outer": "#F4F4F7",  # The background behind the card (for cutouts)
        "bg_card": "#FCFCFD",
        "bg_hero": "#F9F9FB",  # Slightly lighter hero
        "text_primary": "#1C1C1E",
        "text_secondary": "#636366",
        "text_muted": "#AEAEB2",
        "text_subtle": "#8E8E93",
        "accent_green": "#34C759",
        "accent_note": "#444446",
        "divider": "#F2F2F7",  # Lighter divider
        "stripe_black": "#1C1C1E",
        "stripe_border": "#374151",
        "white": "#FFFFFF",
    }

    # Draw the main card background
    img = Image.new("RGB", (WIDTH, HEIGHT), COLORS["bg_outer"])
    draw = ImageDraw.Draw(img)
    # We simulate the card by drawing a rectangle in the center, leaving no margin for this full-bleed version
    # But to support "Cutouts", we need the bg_outer to show through.
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=COLORS["bg_card"])

    fonts = {
        "logo": _load_font("Bold", 54),
        "tagline": _load_font("Bold", 18),
        "status": _load_font("Black", 20),
        "date": _load_font("Medium", 18),
        "naira": _load_font("SemiBold", 36),
        "amount": _load_font("Bold", 108),
        "confirm": _load_font("Medium", 18),
        "label": _load_font("Bold", 19),
        "value": _load_font("Bold", 34),
        "sub_value": _load_font("Medium", 24),
        "note": _load_font("Medium", 26),
        "reference": _load_font("Medium", 22),  # Smaller font for reference
        "footer_label": _load_font("Medium", 15),
        "stripe": _load_font("Bold", 16),
    }

    # --- HELPER FUNCTIONS ---
    def draw_dashed_divider(y_pos):
        """Draws a dashed line to simulate a receipt tear-off."""
        dash_len = 10
        gap_len = 10
        for x in range(MARGIN_X, WIDTH - MARGIN_X, dash_len + gap_len):
            draw.line([(x, y_pos), (x + dash_len, y_pos)], fill=COLORS["divider"], width=2)

    def draw_horizontal_divider(y_pos):
        draw.line([(MARGIN_X, y_pos), (WIDTH - MARGIN_X, y_pos)], fill=COLORS["divider"], width=DIVIDER_WIDTH)

    def draw_standard_row(y_pos, label, value, color=None):
        draw.text((MARGIN_X, y_pos + 5), label.upper(), fill=COLORS["text_muted"], font=fonts["label"])
        val_w = draw.textlength(value, font=fonts["value"])
        draw.text((WIDTH - MARGIN_X - val_w, y_pos), value, fill=color or COLORS["text_primary"], font=fonts["value"])
        return y_pos + ROW_SPACING

    def draw_row_with_subtitle(y_pos, label, value, subtitle):
        draw.text((MARGIN_X, y_pos + 5), label.upper(), fill=COLORS["text_muted"], font=fonts["label"])
        val_w = draw.textlength(value, font=fonts["value"])
        draw.text((WIDTH - MARGIN_X - val_w, y_pos), value, fill=COLORS["text_primary"], font=fonts["value"])
        sub_w = draw.textlength(subtitle, font=fonts["sub_value"])
        draw.text((WIDTH - MARGIN_X - sub_w, y_pos + 45), subtitle, fill=COLORS["text_subtle"], font=fonts["sub_value"])
        return y_pos + ROW_WITH_SUB_SPACING

    def draw_note_row(y_pos, label, note_text):
        draw.text((MARGIN_X, y_pos + 5), label.upper(), fill=COLORS["text_muted"], font=fonts["label"])
        lines = textwrap.wrap(note_text, width=28)
        for i, line in enumerate(lines):
            w = draw.textlength(line, font=fonts["note"])
            draw.text((WIDTH - MARGIN_X - w, y_pos + i * 34), line, fill=COLORS["accent_note"], font=fonts["note"])
        return y_pos + 40 + len(lines) * 34

    # --- HEADER SECTION ---
    draw.text((MARGIN_X, 80), "FUSE", fill=COLORS["text_primary"], font=fonts["logo"])
    draw.text((MARGIN_X, 145), "THE FUTURE OF MONEY", fill=COLORS["text_muted"], font=fonts["tagline"])

    # Status Pill (Simulated with text for now, could be a rounded rect)
    status_txt = "SUCCESSFUL"
    status_w = draw.textlength(status_txt, font=fonts["status"])
    # Optional: Draw a subtle green pill background here if desired
    draw.text((WIDTH - MARGIN_X - status_w, 85), status_txt, fill=COLORS["accent_green"], font=fonts["status"])

    date_str = data.created_at.strftime("%b %d, %Y • %H:%M") if data.created_at else "Jan 01, 2026"
    date_w = draw.textlength(date_str, font=fonts["date"])
    draw.text((WIDTH - MARGIN_X - date_w, 120), date_str, fill=COLORS["text_secondary"], font=fonts["date"])

    # --- HERO AMOUNT SECTION ---
    draw.rectangle([0, 200, WIDTH, 380], fill=COLORS["bg_hero"])

    amount_str = f"{float(data.amount):,.2f}"
    naira_w = draw.textlength("₦", font=fonts["naira"])
    amt_w = draw.textlength(amount_str, font=fonts["amount"])
    total_w = naira_w + amt_w + 15
    start_x = (WIDTH - total_w) / 2

    draw.text((start_x, 292), "₦", fill=COLORS["text_muted"], font=fonts["naira"])
    draw.text((start_x + naira_w + 15, 255), amount_str, fill=COLORS["text_primary"], font=fonts["amount"])

    # --- THE TICKET CUTOUT (The "Physics" Layer) ---
    CUTOUT_Y = 380  # Exactly at the bottom of the hero section
    CUTOUT_RADIUS = 25

    # Left Cutout
    draw.ellipse(
        [-CUTOUT_RADIUS, CUTOUT_Y - CUTOUT_RADIUS, CUTOUT_RADIUS, CUTOUT_Y + CUTOUT_RADIUS], fill=COLORS["bg_outer"]
    )
    # Right Cutout
    draw.ellipse(
        [WIDTH - CUTOUT_RADIUS, CUTOUT_Y - CUTOUT_RADIUS, WIDTH + CUTOUT_RADIUS, CUTOUT_Y + CUTOUT_RADIUS],
        fill=COLORS["bg_outer"],
    )

    # Dashed line connecting them
    draw_dashed_divider(CUTOUT_Y)

    # --- TRANSACTION DETAILS ---
    y_cursor = 460

    # Sender with bank and account info (same format as recipient)
    sender_name = data.sender_name or "Unknown"
    sender_account = getattr(data, "sender_account", None) or ""
    sender_bank = getattr(data, "sender_bank", None) or ""
    if sender_account and sender_bank:
        sender_subtitle = f"{sender_bank} • {sender_account[-4:]}"
        y_cursor = draw_row_with_subtitle(y_cursor, "Sender", sender_name, sender_subtitle)
    elif sender_account:
        sender_subtitle = f"•••• {sender_account[-4:]}"
        y_cursor = draw_row_with_subtitle(y_cursor, "Sender", sender_name, sender_subtitle)
    else:
        y_cursor = draw_standard_row(y_cursor, "Sender", sender_name)
    draw_horizontal_divider(y_cursor)
    y_cursor += SECTION_SPACING

    recipient_subtitle = f"{data.recipient_bank} • {data.recipient_account[-4:]}"
    y_cursor = draw_row_with_subtitle(y_cursor, "Recipient", data.recipient_name, recipient_subtitle)
    draw_horizontal_divider(y_cursor)
    y_cursor += SECTION_SPACING

    # Only show Note row if narration is provided
    if data.narration:
        y_cursor = draw_note_row(y_cursor, "Note", data.narration)
        draw_horizontal_divider(y_cursor)
        y_cursor += SECTION_SPACING

    # Reference - smaller font for the long ID
    draw.text((MARGIN_X, y_cursor + 5), "REFERENCE", fill=COLORS["text_muted"], font=fonts["label"])
    ref_text = data.transaction_reference or "N/A"
    ref_w = draw.textlength(ref_text, font=fonts["reference"])
    draw.text(
        (WIDTH - MARGIN_X - ref_w, y_cursor + 8), ref_text, fill=COLORS["text_secondary"], font=fonts["reference"]
    )
    y_cursor += ROW_SPACING

    # --- EXTENDED FOOTER ---
    footer_height = 180
    footer_y = HEIGHT - footer_height

    # Draw full-width dark footer
    draw.rectangle([0, footer_y, WIDTH, HEIGHT], fill=COLORS["stripe_black"])

    # Top border for footer (subtle)
    draw.line([(0, footer_y), (WIDTH, footer_y)], fill=COLORS["stripe_border"], width=1)

    # Footer content - clean and minimal
    support_text = "Questions? Reach out to us"
    support_w = draw.textlength(support_text, font=fonts["footer_label"])
    draw.text(
        ((WIDTH - support_w) / 2, footer_y + 40), support_text, fill=COLORS["text_muted"], font=fonts["footer_label"]
    )

    # Contact email - prominent
    email_text = "hello@fusepay.com"
    email_w = draw.textlength(email_text, font=fonts["status"])
    draw.text(((WIDTH - email_w) / 2, footer_y + 70), email_text, fill=COLORS["white"], font=fonts["status"])

    # Brand tagline at bottom
    brand_text = "Powered by Fuse"
    brand_w = draw.textlength(brand_text, font=fonts["stripe"])
    draw.text(((WIDTH - brand_w) / 2, HEIGHT - 40), brand_text, fill=COLORS["text_subtle"], font=fonts["stripe"])

    buffer = BytesIO()
    img.save(buffer, format="PNG", quality=95, optimize=True)
    buffer.seek(0)

    return buffer.getvalue()
