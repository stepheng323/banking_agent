"""Models for unified affirmation handling."""

from dataclasses import dataclass, field
from typing import Any, Literal

FlowType = Literal[
    "transfer", "airtime", "beneficiary", "mandate", "data", "utility", "flow_resume"
]


class ConfirmationActions:
    """Standard action names for consistency."""

    FUNDING_APPROVAL = "funding_approval"
    TRANSFER_CONFIRM = "transfer_confirm"
    SAVE_BENEFICIARY = "save_beneficiary"
    REINITIATE_MANDATE = "reinitiate_mandate"
    RESUME_FLOW = "resume_flow"
    AIRTIME_CONFIRM = "airtime_confirm"
    DATA_CONFIRM = "data_confirm"
    BILL_PAYMENT_CONFIRM = "bill_payment_confirm"


@dataclass
class ConfirmationContext:
    """
    Standard context for any flow awaiting user confirmation.

    All flows must store this in conversation_state when awaiting confirmation.
    """

    flow_type: FlowType
    action: str
    callback_data: dict = field(default_factory=dict)
    clarification_prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_type": self.flow_type,
            "action": self.action,
            "callback_data": self.callback_data,
            "clarification_prompt": self.clarification_prompt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConfirmationContext":
        return cls(
            flow_type=data.get("flow_type", "transfer"),
            action=data.get("action", ""),
            callback_data=data.get("callback_data", {}),
            clarification_prompt=data.get("clarification_prompt"),
        )
