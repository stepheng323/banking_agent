"""LangGraph state for query flow."""

from typing import Literal, NotRequired, TypedDict


class QueryState(TypedDict):
    """State for query flow graph."""

    phone_number: str
    message: str
    message_id: str

    flow_state: Literal[
        "parsing",
        "fetching",
        "aggregating",
        "paginating",
        "refining",
        "formatting",
        "complete",
        "error",
    ]
    session_active: bool

    query_type: str
    date_range: dict
    narration_filter: str | None
    transaction_type: str
    limit: int

    # Affordability fields
    amount_check: NotRequired[float | None]
    analysis_type: NotRequired[str | None]
    item_name: NotRequired[str | None]
    projection_months: NotRequired[int | None]
    needs_price_input: NotRequired[bool]

    # Historical stats for relative/simulated analysis
    avg_daily_spend: NotRequired[float | None]
    avg_monthly_net: NotRequired[float | None]

    account_id: str
    account_ids: list[str]
    current_account_index: int
    account_info: dict | None

    current_page: int
    page_size: int
    total_results: int
    has_more: bool
    cached_transactions: list[dict]

    aggregated_result: dict | None

    language: str
    response: str
    continuation_type: NotRequired[Literal["show_more", "filter", "new_query"] | None]
    new_filter: NotRequired[str | None]
