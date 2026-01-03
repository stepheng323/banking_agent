"""LangGraph state for query flow."""

from typing import Literal, NotRequired, TypedDict

from apps.core.src.agent.sub_agents.query.models import NormalizedQuery, QueryResult


class QueryState(TypedDict):
    """State for query flow graph."""

    phone_number: str
    message: str
    message_id: str

    flow_state: Literal[
        "parsing",
        "classifying_continuation",
        "fetching",
        "aggregating",
        "paginating",
        "formatting",
        "complete",
        "error",
        "clarification_needed",
    ]
    session_active: bool

    query: NotRequired[NormalizedQuery | None]
    continuation_type: NotRequired[
        Literal[
            "show_more",
            "time_delta",
            "filter_delta",
            "expand",
            "drill_down",
            "recipient_drill_down",
            "end_session",
            "new_query",
        ]
        | None
    ]

    account_id: str
    account_ids: list[str]
    accounts: NotRequired[list[dict]]
    current_account_index: int
    account_info: dict | None

    current_page: int
    page_size: int
    total_results: int
    has_more: bool
    cached_transactions: list[dict]

    query_result: NotRequired[QueryResult | None]
    aggregated_result: NotRequired[dict | None]

    language: str
    response: str

    clarification_message: NotRequired[str | None]

    # Fault-tolerance fields
    show_expanded: NotRequired[bool]
    recipient_name: NotRequired[str | None]
    filters: NotRequired[dict | None]
    drill_down_index: NotRequired[int | None]
    drill_down_action: NotRequired[str | None]
    clarification_attempts: NotRequired[int]
    last_successful_query: NotRequired[dict | None]
    confidence_level: NotRequired[str]  # "high", "medium", "low"
    user_profile: NotRequired[dict]  # User profile for context
