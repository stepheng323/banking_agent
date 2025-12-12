"""LangGraph state for query flow."""

from typing import Literal, NotRequired, Optional, TypedDict


class QueryState(TypedDict):
    """State for query flow graph."""

    # User context
    phone_number: str
    message: str
    message_id: str

    # Session state
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

    # Query parameters (from parser)
    query_type: str  # balance, total_spent, transaction_list, search, top_recipient, etc.
    date_range: dict  # {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    narration_filter: Optional[str]
    transaction_type: str  # debit, credit, both
    limit: int

    # Account context
    account_id: str
    account_ids: list[str]  # For cross-account queries
    current_account_index: int
    account_info: Optional[dict]

    # Pagination state
    current_page: int
    page_size: int
    total_results: int
    has_more: bool
    cached_transactions: list[dict]

    # Aggregated results
    aggregated_result: Optional[dict]

    # Language for response formatting
    language: str

    # Response
    response: str

    # Continuation tracking
    continuation_type: NotRequired[Optional[Literal["show_more", "filter", "new_query"]]]
    new_filter: NotRequired[Optional[str]]
