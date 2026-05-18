"""Data purchase formatting utilities."""

from shared.formatters.accounts import format_source_account_info_from_account_number
from shared.formatters.currency import coerce_amount, format_amount_number
from shared.i18n import render_message


def format_data_plan_suggestion(
    plan_name: str,
    network: str,
    amount: int,
    validity_days: int | None,
    size_gb: float | None,
    reason: str,
    locale: str = "en",
) -> str:
    """
    Format data plan suggestion message.

    Args:
        plan_name: Full plan name
        network: Network provider
        amount: Price in Naira
        validity_days: Validity in days
        size_gb: Data size in GB
        reason: 'repeat', 'budget', or 'default'

    Returns:
        Formatted suggestion message
    """
    validity = render_message("data.format.validity_days", locale, {"days": validity_days}) if validity_days else ""
    size = f"{size_gb}GB" if size_gb else plan_name

    if reason == "repeat":
        return render_message(
            "data.format.suggestion.repeat",
            locale,
            {"size": size, "network": network, "validity": validity, "amount": f"{amount:,}"},
        )
    elif reason == "budget":
        return render_message(
            "data.format.suggestion.budget",
            locale,
            {"size": size, "network": network, "validity": validity, "amount": f"{amount:,}"},
        )
    else:
        return render_message(
            "data.format.suggestion.default",
            locale,
            {"size": size, "network": network, "validity": validity, "amount": f"{amount:,}"},
        )


def format_data_plan_list(
    plans: list[dict],
    network: str,
    has_more: bool = False,
    locale: str = "en",
) -> str:
    """
    Format paginated data plan list.

    Args:
        plans: List of plan dicts with size_gb, amount, validity_days
        network: Network provider
        has_more: Whether there are more plans to show

    Returns:
        Formatted plan list
    """
    lines = [render_message("data.format.plan_list.header", locale, {"network": network}), ""]

    for plan in plans:
        size = f"{plan.get('size_gb')}GB" if plan.get("size_gb") else plan.get("name", "")
        validity = (
            render_message("data.format.validity_days", locale, {"days": plan.get("validity_days")})
            if plan.get("validity_days")
            else ""
        )
        amount = plan.get("amount", 0)
        lines.append(
            render_message(
                "data.format.plan_list.item",
                locale,
                {"size": size, "amount": f"{amount:,}", "validity": validity},
            )
        )

    lines.append("")
    if has_more:
        lines.append(render_message("data.format.plan_list.reply_show_more", locale))
    else:
        lines.append(render_message("data.format.plan_list.reply_select", locale))

    return "\n".join(lines)


def format_data_summary(data: dict, locale: str = "en") -> str:
    """
    Format a WhatsApp-friendly data purchase confirmation summary.

    Expected keys in data:
      planName: str
      amount: float
      recipientPhone: str
      network: str
      sourceBank: str
      sourceAccount: str
      isSelf: bool
    """
    plan_name = data.get("planName", render_message("data.format.summary.plan_name_fallback", locale))
    amount = coerce_amount(data.get("amount"))
    recipient_phone = str(data.get("recipientPhone") or "")
    network = str(data.get("network") or "")
    source_bank = str(data.get("sourceBank") or render_message("data.format.summary.source_bank_fallback", locale))
    source_account = str(data.get("sourceAccount") or "")
    is_self = data.get("isSelf", False)

    target_display = render_message("data.format.summary.target_self", locale) if is_self else recipient_phone

    lines = [
        render_message(
            "data.format.summary.title",
            locale,
            {"plan_name": plan_name, "target_display": target_display},
        ),
        render_message(
            "data.format.summary.network_amount",
            locale,
            {"network": network, "amount": format_amount_number(amount)},
        ),
    ]

    lines.append("")
    lines.append(
        format_source_account_info_from_account_number(
            bank=source_bank,
            account_number=source_account,
            locale=locale,
        )
    )

    return "\n".join(lines)
