"""Pydantic models for flow webhook requests and responses."""

from typing import Any

from pydantic import BaseModel


class FlowDataExchangeRequest(BaseModel):
    """Request model for flow data exchange."""

    version: str | None = None
    flow_token: str | None = None
    screen: str | None = None
    data: dict[str, Any] | None = None


class FlowAction(BaseModel):
    """Response model for flow actions."""

    action: str
    next_screen: str | None = None
    data: dict[str, Any] | None = None
