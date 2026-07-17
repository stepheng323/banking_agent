"""Beneficiary display formatting."""

from __future__ import annotations

from typing import Any

from banking.presentation.i18n.renderer import render_message
from shared.messaging.body_blocks import MessageDocument, render_body_blocks_text


class BeneficiaryFormatter:
    """Format beneficiary surfaces for chat presentation."""

    @staticmethod
    def format_beneficiary_list(
        beneficiaries: list[Any],
        *,
        locale: str = "en",
        name_filter: str | None = None,
        has_next: bool = False,
    ) -> str:
        """Format saved beneficiaries as fallback text."""
        return render_body_blocks_text(
            BeneficiaryFormatter.format_beneficiary_list_blocks(
                beneficiaries,
                locale=locale,
                name_filter=name_filter,
                has_next=has_next,
            )
        )

    @staticmethod
    def format_beneficiary_list_blocks(
        beneficiaries: list[Any],
        *,
        locale: str = "en",
        name_filter: str | None = None,
        has_next: bool = False,
    ) -> MessageDocument | None:
        """Format saved beneficiaries as mobile-friendly message blocks."""
        if not beneficiaries:
            return None

        heading = (
            render_message("beneficiary.list.filtered_header", locale, {"filter": name_filter})
            if name_filter
            else render_message("beneficiary.list.header", locale).strip("*")
        )
        blocks: MessageDocument = [{"type": "heading", "text": heading}]
        for index, beneficiary in enumerate(beneficiaries, 1):
            alias = _beneficiary_value(beneficiary, "alias")
            account_name = _beneficiary_value(beneficiary, "account_name", "name")
            display_name = alias or account_name or "Beneficiary"
            bank_name = _beneficiary_value(beneficiary, "bank_name", "bank")
            account_number = _beneficiary_value(beneficiary, "account_number", "account")
            title = f"{index}. {display_name}"

            if account_name and account_name.lower() != display_name.lower():
                title = f"{title} — {account_name}"

            detail_parts = []
            if bank_name:
                detail_parts.append(bank_name)
            if account_number:
                detail_parts.append(_mask_account(account_number))
            lines = [title]
            if detail_parts:
                lines.append(" • ".join(detail_parts))

            blocks.append({"type": "text", "text": "\n".join(lines)})

        if has_next:
            blocks.append({"type": "text", "text": render_message("common.pagination.more", locale)})

        return blocks

    @staticmethod
    def format_count_preview_blocks(
        beneficiaries: list[Any],
        count_line: str,
        *,
        locale: str = "en",
        preview_limit: int = 3,
    ) -> MessageDocument | None:
        """Format a beneficiary count answer with a short preview."""
        blocks: MessageDocument = [{"type": "text", "text": count_line}]
        preview = BeneficiaryFormatter.format_beneficiary_list_blocks(
            beneficiaries[:preview_limit],
            locale=locale,
        )
        if preview:
            blocks.append({"type": "heading", "text": render_message("beneficiary.list.preview_header", locale)})
            blocks.extend(preview[1:])
        return blocks


def _beneficiary_value(beneficiary: Any, *names: str) -> str:
    for name in names:
        value = beneficiary.get(name) if isinstance(beneficiary, dict) else getattr(beneficiary, name, None)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _mask_account(account_number: str) -> str:
    digits = "".join(ch for ch in str(account_number) if ch.isdigit())
    suffix = digits[-4:] if digits else "????"
    return f"···{suffix}"


__all__ = ["BeneficiaryFormatter"]
