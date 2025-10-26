"""Message models for queue communication."""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class MessageType(str, Enum):
    """Type of message received."""

    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    CONTACT = "contact"


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
    text: Optional[str] = Field(None, description="Text content")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    retry_count: int = Field(default=0, description="Number of processing attempts")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ProcessedMessage(BaseModel):
    """Result of message processing."""

    message_id: str
    from_number: str
    intent: Optional[str] = None
    entities: Dict[str, Any] = Field(default_factory=dict)
    response: Optional[str] = None
    actions: list[str] = Field(default_factory=list)
    processed_at: datetime = Field(default_factory=datetime.utcnow)
    success: bool = True
    error: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
