"""Pydantic models for flow webhook requests and responses."""

from typing import Any, Dict, Optional
from pydantic import BaseModel


class FlowDataExchangeRequest(BaseModel):
    """Request model for flow data exchange."""

    version: Optional[str] = None
    flow_token: Optional[str] = None
    screen: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class FlowAction(BaseModel):
    """Response model for flow actions."""

    action: str
    next_screen: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

