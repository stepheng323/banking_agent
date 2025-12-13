"""LangGraph state for query flow."""

from typing import Literal, NotRequired, Optional, TypedDict


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
        "error"
    ]
    session_active: bool

    query_type: str
    date_range: dict
    narration_filter: Optional[str]
    transaction_type: str
    limit: int

    account_id: str
    account_ids: list[str]
    current_account_index: int
    account_info: Optional[dict]

    current_page: int
    page_size: int
    total_results: int
    has_more: bool
    cached_transactions: list[dict]

    aggregated_result: Optional[dict]

    language: str
    response: str
    continuation_type: NotRequired[Optional[Literal["show_more", "filter", "new_query"]]]
    new_filter: NotRequired[Optional[str]]
