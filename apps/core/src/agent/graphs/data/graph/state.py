"""State for the data purchase graph."""

from typing import Any, Literal, TypedDict

from apps.core.src.agent.graphs.data.models import DataPlan


class DataPurchaseState(TypedDict, total=False):
    """State for the data purchase flow."""

    phone_number: str
    user_id: str
    user_profile: dict[str, Any]

    message: str
    message_id: str
    target_phone: str
    source: Literal["self", "other"]
    network: str

    budget: int | None
    suggested_plan: DataPlan | None
    selected_plan: DataPlan | None
    all_plans: list[DataPlan]

    # Authorization fields
    idempotency_key: str | None
    pin_verified: bool
    pin_verification_error: str | None
    pin_retry_count: int
    data_status: Literal["pending", "authorized", "completed", "failed"] | None

    flow_state: Literal[
        "resolving",
        "suggesting",
        "awaiting_confirmation",
        "listing",
        "confirming",
        "authorizing",
        "executing",
        "completed",
        "error",
    ]
    suggestion_attempts: int
    response: str
    error: str | None
    user_context: dict[str, Any]
    last_data_purchase: dict[str, Any] | None


