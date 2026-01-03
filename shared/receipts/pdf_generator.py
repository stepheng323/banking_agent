"""PDF receipt generator using ReportLab."""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from shared.receipts.models import TransferReceiptData
from shared.receipts.utils import format_datetime, format_naira, mask_account

# Colors
PRIMARY_COLOR = colors.HexColor("#1a1a2e")
SUCCESS_COLOR = colors.HexColor("#10b981")
ACCENT_COLOR = colors.HexColor("#6366f1")
LIGHT_GRAY = colors.HexColor("#f3f4f6")
DARK_GRAY = colors.HexColor("#4b5563")


def generate_transfer_receipt(data: TransferReceiptData) -> bytes:
    """Generate a PDF receipt for a successful transfer.

    Args:
        data: TransferReceiptData containing all receipt information

    Returns:
        PDF content as bytes
    """
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    elements = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=24,
        textColor=PRIMARY_COLOR,
        spaceAfter=5 * mm,
        alignment=1,  # Center
    )

    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=DARK_GRAY,
        alignment=1,
    )

    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading3"],
        fontSize=12,
        textColor=PRIMARY_COLOR,
        spaceBefore=8 * mm,
        spaceAfter=3 * mm,
    )

    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=9,
        textColor=DARK_GRAY,
    )

    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=11,
        textColor=PRIMARY_COLOR,
    )

    # Header
    elements.append(Paragraph("Transfer Receipt", title_style))
    elements.append(Paragraph(f"Reference: {data.transaction_reference}", subtitle_style))
    elements.append(Spacer(1, 8 * mm))

    # Status badge
    status_data = [["✓ SUCCESSFUL"]]
    status_table = Table(status_data, colWidths=[80 * mm])
    status_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SUCCESS_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("ROUNDRECT", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(status_table)
    elements.append(Spacer(1, 8 * mm))

    # Amount block
    amount_data = [
        [Paragraph("Amount Transferred", label_style)],
        [
            Paragraph(
                format_naira(data.amount),
                ParagraphStyle(
                    "BigAmount",
                    fontSize=28,
                    textColor=PRIMARY_COLOR,
                    alignment=1,
                ),
            )
        ],
    ]
    amount_table = Table(amount_data, colWidths=[170 * mm])
    amount_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(amount_table)
    elements.append(Spacer(1, 5 * mm))

    # Recipient section
    elements.append(Paragraph("Recipient", section_header_style))
    recipient_data = [
        ["Name", data.recipient_name],
        ["Account", mask_account(data.recipient_account)],
        ["Bank", data.recipient_bank],
    ]
    recipient_table = Table(recipient_data, colWidths=[50 * mm, 120 * mm])
    recipient_table.setStyle(
        TableStyle(
            [
                ("TEXTCOLOR", (0, 0), (0, -1), DARK_GRAY),
                ("TEXTCOLOR", (1, 0), (1, -1), PRIMARY_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, LIGHT_GRAY),
            ]
        )
    )
    elements.append(recipient_table)

    # Debit sources section
    if data.debit_sources:
        if data.is_pooled:
            elements.append(Paragraph("Debited Accounts (Pooled Transfer)", section_header_style))
        else:
            elements.append(Paragraph("Debited Account", section_header_style))

        debit_data = []
        for source in data.debit_sources:
            debit_data.append(
                [
                    f"{source.bank_name} ({mask_account(source.account_number)})",
                    format_naira(source.amount),
                ]
            )

        debit_table = Table(debit_data, colWidths=[110 * mm, 60 * mm])
        debit_table.setStyle(
            TableStyle(
                [
                    ("TEXTCOLOR", (0, 0), (0, -1), PRIMARY_COLOR),
                    ("TEXTCOLOR", (1, 0), (1, -1), PRIMARY_COLOR),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.5, LIGHT_GRAY),
                ]
            )
        )
        elements.append(debit_table)

    # Fee and total
    elements.append(Paragraph("Summary", section_header_style))
    summary_data = [
        ["Transfer Amount", format_naira(data.amount)],
        ["Fee", format_naira(data.fee)],
        ["Total Debited", format_naira(data.total_debited)],
    ]
    summary_table = Table(summary_data, colWidths=[110 * mm, 60 * mm])
    summary_table.setStyle(
        TableStyle(
            [
                ("TEXTCOLOR", (0, 0), (-1, -2), DARK_GRAY),
                ("TEXTCOLOR", (1, 0), (-1, -2), PRIMARY_COLOR),
                ("TEXTCOLOR", (0, -1), (-1, -1), PRIMARY_COLOR),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, LIGHT_GRAY),
                ("LINEABOVE", (0, -1), (-1, -1), 1, PRIMARY_COLOR),
            ]
        )
    )
    elements.append(summary_table)

    # Narration if present
    if data.narration:
        elements.append(Paragraph("Narration", section_header_style))
        elements.append(Paragraph(data.narration, value_style))

    # Transaction details
    elements.append(Paragraph("Transaction Details", section_header_style))
    details_data = [
        ["Reference", data.transaction_reference],
        ["Date & Time", format_datetime(data.created_at)],
    ]
    if data.sender_name:
        details_data.insert(0, ["Sender", data.sender_name])

    details_table = Table(details_data, colWidths=[50 * mm, 120 * mm])
    details_table.setStyle(
        TableStyle(
            [
                ("TEXTCOLOR", (0, 0), (0, -1), DARK_GRAY),
                ("TEXTCOLOR", (1, 0), (1, -1), PRIMARY_COLOR),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(details_table)

    # Footer
    elements.append(Spacer(1, 15 * mm))
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=DARK_GRAY,
        alignment=1,
    )
    elements.append(Paragraph("This is an official transaction receipt. For queries, contact support.", footer_style))

    doc.build(elements)

    return buffer.getvalue()
