"""UI Intents for the Presentation Layer.

These dataclasses represent abstract user interface intents emitted by the Orchestrator.
They are channel-neutral and strictly define *what* needs to happen, not *how*.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class UiIntent:
    """Base class for all UI intents."""

    pass


@dataclass
class Say(UiIntent):
    """Simple text response."""

    text: str


@dataclass
class Ask(UiIntent):
    """Request information from the user."""

    text: str
    fields: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)


@dataclass
class ShowOptions(UiIntent):
    """Present a list of options to the user."""

    title: str
    options: list[dict[str, str]]
    task_ids: list[str] = field(default_factory=list)


@dataclass
class RequestConfirmation(UiIntent):
    """Request confirmation for a set of tasks."""

    task_ids: list[str]
    summary: str
    token: str
    correlation_id: str


@dataclass
class RequestAuth(UiIntent):
    """Request authentication (PIN/OTP)."""

    method: Literal["pin", "otp"]
    task_ids: list[str]
    correlation_id: str
    reason: str | None = None  # Semantic reason (e.g. "Transfer Authorization")
    summary: str | None = None  # Specific details (e.g. "Send 5k to Mum")


@dataclass
class ShowReceipt(UiIntent):
    """Display a receipt for a completed task."""

    task_id: str
    receipt: dict[str, Any]
    caption: str = ""
