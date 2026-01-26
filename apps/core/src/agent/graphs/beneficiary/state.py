from typing import TypedDict

from apps.core.src.agent.graphs.beneficiary.models import BeneficiaryPayload


class BeneficiaryState(TypedDict):
    """State for beneficiary graph execution."""

    payload: BeneficiaryPayload
    user_id: str
    phone_number: str

    # Internal flow control
    flow_state: str  # "parsing", "executing", "complete"
    error: str | None
    response: str | None
