"""Message models for queue communication."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Type of message received."""

    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    CONTACT = "contact"
    FLOW = "flow"


class MessagePriority(int, Enum):
    """Message processing priority."""

    LOW = 1
    NORMAL = 5
    HIGH = 10
    URGENT = 20


class WhatsAppMessage(BaseModel):
    """WhatsApp message to be processed."""

    message_id: str = Field(..., description="WhatsApp message ID")
    from_number: str = Field(..., description="Sender's phone number")
    message_type: MessageType = Field(..., description="Type of message")
    text: str | None = Field(None, description="Text content")
    flow_data: dict[str, Any] | None = Field(None, description="Flow response data")
    media_id: str | None = Field(None, description="Media ID for download")
    mime_type: str | None = Field(None, description="MIME type of media")
    quoted_message_id: str | None = Field(None, description="ID of quoted/replied message")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    channel: str = "whatsapp"
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    retry_count: int = Field(default=0, description="Number of processing attempts")

    class Config:
        """Pydantic config."""

        json_encoders = {datetime: lambda v: v.isoformat()}


class ProcessedMessage(BaseModel):
    """Result of message processing."""

    message_id: str
    from_number: str
    intent: str | None = None
    entities: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None
    actions: list[str] = Field(default_factory=list)
    processed_at: datetime = Field(default_factory=datetime.utcnow)
    success: bool = True
    error: str | None = None

    class Config:
        """Pydantic config."""

        json_encoders = {datetime: lambda v: v.isoformat()}
