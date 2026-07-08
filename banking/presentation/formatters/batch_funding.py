"""Batch funding shortfall formatter."""

from collections import OrderedDict
from typing import Any

from banking.presentation.formatters.currency import coerce_amount, format_naira
from banking.presentation.i18n.renderer import render_message
from shared.money import MoneyAmount


def format_batch_funding_shortfall(
    shortfalls: list[Any],
    total_demanded: MoneyAmount,
    total_available: MoneyAmount,
    source_options: list[Any] | None = None,
    anchor_source_ids: list[str] | None = None,
    locale: str = "en",
) -> str:
    """Format a deterministic shortfall message for batch funding failures."""
    header = render_message("funding.batch.shortfall_header", locale)
    body = render_message(
        "funding.batch.shortfall_body",
        locale,
        {"demanded": format_naira(total_demanded), "available": format_naira(total_available)},
    )

    lines: list[str] = [header, "", body]
    anchor = _anchor_line(source_options, anchor_source_ids)
    if anchor:
        lines.extend(["", anchor])
    lines.append("")
    for index, shortfall in enumerate(shortfalls, start=1):
        account_requested = str(
            getattr(shortfall, "account_requested", "") or render_message("funding.format.plan.bank_fallback", locale)
        )
        amount_needed = coerce_amount(getattr(shortfall, "amount_needed", 0))
        account_available = coerce_amount(getattr(shortfall, "account_available", 0))
        deficit = coerce_amount(getattr(shortfall, "deficit", max(coerce_amount(0), amount_needed - account_available)))
        task_label = _shortfall_label(shortfall, index=index)

        if account_available > 0:
            lines.append(
                render_message(
                    "funding.batch.task_covered",
                    locale,
                    {"task_id": task_label, "amount": format_naira(account_available), "bank": account_requested},
                )
            )
        lines.append(
            render_message(
                "funding.batch.task_short",
                locale,
                {
                    "task_id": task_label,
                    "needed": format_naira(amount_needed),
                    "available": format_naira(account_available),
                    "bank": account_requested,
                    "deficit": format_naira(deficit),
                },
            )
        )

        alternates = getattr(shortfall, "alternate_accounts", None) or []
        if alternates:
            top = alternates[0]
            alt_bank = str(top.get("bank_name") or render_message("funding.format.plan.bank_fallback", locale))
            alt_available = format_naira(top.get("available"))
            lines.append(
                render_message(
                    "funding.batch.alternate_suggestion",
                    locale,
                    {"bank": alt_bank, "available": alt_available},
                )
            )
        lines.append("")

    lines.append(render_message("funding.batch.total_infeasible", locale))
    lines.extend(_source_option_lines(source_options))
    return "\n".join(lines).strip()


def _last4(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else "????"


def _shortfall_label(shortfall: Any, *, index: int) -> str:
    for attr in ("recipient_name", "recipient_resolved_name", "label"):
        value = str(getattr(shortfall, attr, "") or "").strip()
        if value:
            return value
    return f"Transfer {index}"


def _source_option_id(option: Any) -> str:
    return str(getattr(option, "account_id", "") or "").strip()


def _source_option_bank(option: Any) -> str:
    bank = str(getattr(option, "bank_name", "") or getattr(option, "bank", "") or "Bank").strip()
    last4 = str(getattr(option, "last4", "") or getattr(option, "account_number_last4", "")).strip()
    if last4 and len(last4) == 4 and last4 != "0000":
        return f"{bank} (···{last4})"
    return bank


def _source_option_available(option: Any) -> MoneyAmount:
    return coerce_amount(getattr(option, "available", 0))


def _source_option_label(option: Any, *, include_account: bool = True) -> str:
    bank = _source_option_bank(option)
    markers: list[str] = []
    if bool(getattr(option, "is_default", False)):
        markers.append("default")
    if bool(getattr(option, "is_selected", False)):
        markers.append("selected")
    marker_text = f" ({', '.join(markers)})" if markers else ""
    if not include_account:
        return f"{bank}{marker_text}"
    return f"{bank} (···{_last4(getattr(option, 'account_number', ''))}){marker_text}"


def _source_options_by_id(source_options: list[Any] | None) -> dict[str, Any]:
    return {_source_option_id(option): option for option in source_options or [] if _source_option_id(option)}


def _anchor_line(source_options: list[Any] | None, anchor_source_ids: list[str] | None) -> str | None:
    by_id = _source_options_by_id(source_options)
    anchors = [by_id[source_id] for source_id in anchor_source_ids or [] if source_id in by_id]
    if not anchors:
        return None
    if len(anchors) == 1:
        option = anchors[0]
        available = format_naira(_source_option_available(option))
        bank = _source_option_bank(option)
        if bool(getattr(option, "is_selected", False)):
            return f"Your selected {bank} has {available}."
        if bool(getattr(option, "is_default", False)):
            return f"Your default {bank} has {available}."
        return f"Your {bank} has {available}."
    total = sum((_source_option_available(option) for option in anchors), coerce_amount(0))
    has_selected_anchor = any(bool(getattr(option, "is_selected", False)) for option in anchors)
    label = "selected sources" if has_selected_anchor else "source accounts"
    return f"Your {label} have {format_naira(total)} available."


def _source_option_lines(
    source_options: list[Any] | None,
    *,
    limit: int = 4,
    numbered: bool = True,
    include_header: bool = True,
    option_ids: list[str] | None = None,
) -> list[str]:
    if not source_options:
        return []
    selected_ids = set(option_ids or [])
    options = [option for option in source_options if not selected_ids or _source_option_id(option) in selected_ids]
    if not options:
        return []
    lines = ["", "Available sources:"] if include_header else []
    for index, option in enumerate(options[:limit], start=1):
        label = _source_option_label(option)
        available = format_naira(_source_option_available(option))
        prefix = f"{index}. " if numbered else "• "
        lines.append(f"{prefix}{label}: {available}")
    if len(options) > limit:
        lines.append(f"+ {len(options) - limit} more linked account(s)")
    return lines


def _source_names_for_ids(source_options: list[Any] | None, source_ids: list[str] | None) -> list[str]:
    by_id = _source_options_by_id(source_options)
    return [_source_option_bank(by_id[source_id]) for source_id in source_ids or [] if source_id in by_id]


def format_batch_source_choice_request(
    *,
    total_demanded: MoneyAmount,
    remaining_amount: MoneyAmount,
    candidate_source_ids: list[str],
    source_options: list[Any] | None = None,
    anchor_source_ids: list[str] | None = None,
    locale: str = "en",
) -> str:
    """Ask the user to choose when multiple implicit source accounts can cover the gap."""
    del locale
    by_id = _source_options_by_id(source_options)
    candidates = [by_id[source_id] for source_id in candidate_source_ids if source_id in by_id]
    candidate_bank_names = [f"**{_source_option_bank(option)}**" for option in candidates]

    if len(candidate_bank_names) == 2:
        options_text = f"{candidate_bank_names[0]} or {candidate_bank_names[1]}"
    elif len(candidate_bank_names) > 2:
        options_text = ", ".join(candidate_bank_names[:-1]) + f", or {candidate_bank_names[-1]}"
    else:
        options_text = "another linked account with enough balance"

    lines = []

    anchor = _anchor_line(source_options, anchor_source_ids)
    if anchor:
        # e.g. "Your selected Access Bank has ₦30,000.00." -> "but your selected Access Bank has ₦30,000.00"
        lines.append(f"This batch needs {format_naira(total_demanded)}, but {anchor.lower().replace('.', '')}.")
    else:
        lines.append(f"This batch needs {format_naira(total_demanded)}.")

    lines.append("")
    lines.append(f"You can pool from one additional account to cover the remaining {format_naira(remaining_amount)}.")
    lines.append(f"Which would you like to use: {options_text}?")

    return "\n".join(lines).strip()


def _only_added_source_can_cover_line(
    *,
    plans_by_task: dict[str, Any],
    source_options: list[Any] | None,
    anchor_source_ids: list[str] | None,
    suggested_source_ids: list[str] | None,
) -> str | None:
    anchor_ids = set(anchor_source_ids or [])
    added_ids = [source_id for source_id in suggested_source_ids or [] if source_id not in anchor_ids]
    if len(added_ids) != 1:
        return None
    added_id = added_ids[0]
    remaining_amount = sum(
        (
            coerce_amount(getattr(step, "amount", 0))
            for plan in plans_by_task.values()
            for step in getattr(plan, "steps", []) or []
            if str(getattr(step, "account_id", "")) == added_id
        ),
        coerce_amount(0),
    )
    if remaining_amount <= 0:
        return None
    by_id = _source_options_by_id(source_options)
    added_option = by_id.get(added_id)
    if added_option is None:
        return None
    eligible = [
        option
        for option in source_options or []
        if _source_option_id(option) not in anchor_ids and _source_option_available(option) >= remaining_amount
    ]
    if len(eligible) != 1 or _source_option_id(eligible[0]) != added_id:
        return None
    return f"Only {_source_option_bank(added_option)} can cover the remaining {format_naira(remaining_amount)}."


def format_batch_funding_approval_request(
    *,
    plans_by_task: dict[str, Any],
    total_demanded: MoneyAmount,
    source_options: list[Any] | None = None,
    anchor_source_ids: list[str] | None = None,
    suggested_source_ids: list[str] | None = None,
    locale: str = "en",
) -> str:
    """Format an opt-in prompt for an otherwise feasible implicit pooled plan."""
    del locale
    source_totals: OrderedDict[str, MoneyAmount] = OrderedDict()
    for plan in plans_by_task.values():
        for step in getattr(plan, "steps", []) or []:
            bank = str(getattr(step, "bank_name", "") or "Bank").strip()
            amount = coerce_amount(getattr(step, "amount", 0))
            if amount <= 0:
                continue
            source_totals[bank] = source_totals.get(bank, coerce_amount(0)) + amount

    anchor_ids = set(anchor_source_ids or [])
    added_ids = [source_id for source_id in suggested_source_ids or [] if source_id not in anchor_ids]
    added_source_names = _source_names_for_ids(source_options, added_ids)

    if len(source_totals) > 1 and anchor_source_ids and added_source_names:
        by_id = _source_options_by_id(source_options)
        anchor_options = [by_id[sid] for sid in anchor_source_ids if sid in by_id]
        if anchor_options:
            anchor_bank = _source_option_bank(anchor_options[0])
            anchor_available = format_naira(_source_option_available(anchor_options[0]))
            added_id = added_ids[0]
            added_amount = sum(
                (
                    coerce_amount(getattr(step, "amount", 0))
                    for plan in plans_by_task.values()
                    for step in getattr(plan, "steps", []) or []
                    if str(getattr(step, "account_id", "")) == added_id
                ),
                coerce_amount(0),
            )

            lines = [
                f"This batch needs {format_naira(total_demanded)}, but your {anchor_bank} only has {anchor_available}.",
                ""
            ]

            eligible_candidates = [
                option for option in source_options or []
                if _source_option_id(option) not in anchor_ids and _source_option_available(option) > 0
            ]

            if not eligible_candidates:
                lines.append(
                    "Because we can only pool from one additional account, and none of your other "
                    f"accounts have the remaining {format_naira(added_amount)} available, "
                    "this transfer cannot be completed right now."
                )
                lines.append("")
                lines.append("You can reduce the transfer amount, or top up one of your accounts first.")
            else:
                lines.append(
                    f"You can pool from an additional account to cover the remaining {format_naira(added_amount)}. "
                    "Which would you like to use?"
                )
                for index, option in enumerate(eligible_candidates, start=1):
                    bank_name = _source_option_bank(option)
                    available = _source_option_available(option)
                    lines.append(f"{index}. {bank_name} ({format_naira(available)} available)")
                lines.append("")
                lines.append("Reply with the number or bank name.")

            return "\n".join(lines).strip()

    # Fallback/explicit pooling scenario
    parts = [f"{format_naira(amount)} from {bank}" for bank, amount in source_totals.items()]
    if len(parts) == 2:
        split_text = f"{parts[0]} and {parts[1]}"
    elif len(parts) > 2:
        split_text = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    else:
        split_text = parts[0]

    lines = [
        f"To authorize pulling {split_text} to cover the {format_naira(total_demanded)} total, reply **confirm**,"
        f" or tell me if you prefer a different split."
    ]
    return "\n".join(lines).strip()


def format_batch_source_cap_shortfall(
    *,
    total_demanded: MoneyAmount,
    capped_available: MoneyAmount,
    max_source_accounts: int,
    is_fixable_by_changing_sources: bool = False,
    source_options: list[Any] | None = None,
    anchor_source_ids: list[str] | None = None,
    pool_source_ids: list[str] | None = None,
    locale: str = "en",
) -> str:
    """Format a shortfall caused by the policy cap on pooled source accounts."""
    del locale
    deficit = max(coerce_amount(0), coerce_amount(total_demanded) - coerce_amount(capped_available))

    lines = [f"This batch needs {format_naira(total_demanded)}.", ""]

    pool_banks = []
    if pool_source_ids and source_options:
        by_id = _source_options_by_id(source_options)
        pool_banks = [f"**{_source_option_bank(by_id[sid])}**" for sid in pool_source_ids if sid in by_id]

    pool_text = " + ".join(pool_banks) if pool_banks else "your highest balances"

    if is_fixable_by_changing_sources:
        anchor = _anchor_line(source_options, anchor_source_ids)
        if anchor:
            lines.append(f"{anchor}")
        lines.append(
            f"Since we can only pool up to {max_source_accounts} accounts, "
            f"keeping this selection leaves you short {format_naira(deficit)}."
        )
        lines.append("")
        lines.append(
            "You can reduce an amount, or choose two entirely different source accounts "
            "with higher balances."
        )
    else:
        lines.append(
            f"Because transfers are limited to a maximum of {max_source_accounts} pooled accounts, "
            f"even combining {pool_text} only reaches {format_naira(capped_available)}."
        )
        lines.append("")
        lines.append(f"You are short {format_naira(deficit)}. Please reduce a transfer amount or cancel the batch.")

    return "\n".join(lines).strip()
