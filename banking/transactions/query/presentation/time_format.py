from datetime import timedelta

from banking.presentation.i18n.renderer import render_message
from banking.transactions.query.models.domain import QueryRequest
from banking.transactions.query.utils.timezone import lagos_today


def build_timeframe_suffix(query: QueryRequest, locale: str) -> str:
    time_range = query.time_range
    if time_range:
        today = lagos_today()
        if time_range.start == time_range.end == today:
            return render_message("query.analytics.timeframe_today", locale)
        yesterday = today - timedelta(days=1)
        if time_range.start == time_range.end == yesterday:
            return render_message("query.analytics.timeframe_yesterday", locale)

        if time_range.granularity == "month":
            if time_range.start.month == today.month and time_range.start.year == today.year:
                return " this month"
            else:
                return f" in {time_range.start.strftime('%B %Y')}"

        if time_range.end == today and (today - time_range.start).days in {29, 30}:
            return " in the last 30 days"

        if time_range.start == time_range.end:
            return render_message(
                "query.analytics.timeframe_on_date",
                locale,
                {"date": time_range.start.strftime("%b %d")},
            )
        return render_message(
            "query.analytics.timeframe_range",
            locale,
            {
                "start": time_range.start.strftime("%b %d"),
                "end": time_range.end.strftime("%b %d"),
            },
        )
    return render_message("query.analytics.timeframe_default", locale)
