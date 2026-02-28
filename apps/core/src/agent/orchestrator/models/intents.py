"""UI Intents for the Presentation Layer.

These dataclasses represent abstract user interface intents emitted by the Orchestrator.
They are channel-neutral and strictly define *what* needs to happen, not *how*.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class UiIntent:
    """Base class for all UI intents."""

    actionable_payload: dict[str, Any] | None = field(default=None, kw_only=True)

    def to_dict(self) -> dict[str, Any]:
        """Convert intent to dictionary."""
        raise NotImplementedError("Subclasses must implement to_dict")


@dataclass
class Say(UiIntent):
    """Simple text response."""

    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "say", "text": self.text, "actionable_payload": self.actionable_payload}


@dataclass
class Ask(UiIntent):
    """Request information from the user."""

    text: str
    fields: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "ask",
            "text": self.text,
            "fields": self.fields,
            "task_ids": self.task_ids,
            "actionable_payload": self.actionable_payload,
        }


@dataclass
class ShowOptions(UiIntent):
    """Present a list of options to the user."""

    title: str
    options: list[dict[str, str]]
    task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "show_options",
            "title": self.title,
            "options": self.options,
            "task_ids": self.task_ids,
            "actionable_payload": self.actionable_payload,
        }


@dataclass
class RequestConfirmation(UiIntent):
    """Request confirmation for a set of tasks."""

    task_ids: list[str]
    summary: str
    token: str
    correlation_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "request_confirmation",
            "task_ids": self.task_ids,
            "summary": self.summary,
            "idempotency_key": self.correlation_id,
            "actionable_payload": self.actionable_payload,
        }


@dataclass
class RequestAuth(UiIntent):
    """Request authentication (PIN/OTP)."""

    method: Literal["pin", "otp"]
    task_ids: list[str]
    correlation_id: str
    reason: str | None = None  # Semantic reason (e.g. "Transfer Authorization")
    summary: str | None = None  # Specific details (e.g. "Send 5k to Mum")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "auth_request",
            "method": self.method,
            "task_ids": self.task_ids,
            "idempotency_key": self.correlation_id,
            "header": self.reason,
            "summary": self.summary,
            "actionable_payload": self.actionable_payload,
        }


@dataclass
class ShowReceipt(UiIntent):
    """Display a receipt for a completed task."""

    task_id: str
    receipt: dict[str, Any]
    caption: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "show_receipt",
            "task_id": self.task_id,
            "receipt": self.receipt,
            "caption": self.caption,
            "actionable_payload": self.actionable_payload,
        }


@dataclass
class ShowFlow(UiIntent):
    """Request a channel flow to be presented."""

    flow_id: str
    flow_config: dict[str, Any]
    fallback_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "flow",
            "flow_id": self.flow_id,
            "flow_config": self.flow_config,
            "fallback_text": self.fallback_text,
            "actionable_payload": self.actionable_payload,
        }


def reconstruct_intent(data: dict[str, Any]) -> UiIntent | None:
    """Reconstruct high-level intent from dictionary (outbox format)."""
    msg_type = data.get("type")

    if msg_type == "say":
        intent: UiIntent = Say(text=data["text"])
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    elif msg_type == "auth_request":
        intent = RequestAuth(
            method=data.get("method", "pin"),
            task_ids=data.get("task_ids", []),
            correlation_id=data.get("idempotency_key", "unknown"),
            reason=data.get("header"),
            summary=data.get("summary"),
        )
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    elif msg_type == "request_confirmation":
        intent = RequestConfirmation(
            task_ids=data.get("task_ids", []),
            summary=data.get("summary", ""),
            correlation_id=data.get("idempotency_key", "unknown"),
            token=data.get("idempotency_key", "unknown"),
        )
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    elif msg_type == "show_receipt":
        intent = ShowReceipt(
            task_id=data.get("task_id", "unknown"),
            receipt=data.get("receipt", {}),
            caption=data.get("caption", ""),
        )
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    elif msg_type == "image" and "receipt" in data.get("caption", "").lower():
        # Legacy/Image based receipt
        intent = ShowReceipt(task_id="unknown", receipt={"url": data["url"]}, caption=data.get("caption", ""))
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    elif msg_type == "flow":
        intent = ShowFlow(
            flow_id=data.get("flow_id", ""),
            flow_config=data.get("flow_config", {}),
            fallback_text=data.get("fallback_text", ""),
        )
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    elif msg_type == "show_options":
        intent = ShowOptions(
            title=data.get("title", ""),
            options=data.get("options", []),
            task_ids=data.get("task_ids", []),
        )
        intent.actionable_payload = data.get("actionable_payload")
        return intent

    return None
