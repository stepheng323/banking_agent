"""Channel-agnostic message models for multi-channel support.

These models replace WhatsApp-specific models to enable the banking agent
to work with any messaging platform.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """Supported messaging channels."""

    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    SMS = "sms"
    WEB = "web"
    VOICE = "voice"


class MessageType(str, Enum):
    """Type of message content."""

    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    CONTACT = "contact"
    INTERACTIVE = "interactive"


class MessagePriority(int, Enum):
    """Message processing priority."""

    LOW = 1
    NORMAL = 5
    HIGH = 10
    URGENT = 20


class ChannelMessage(BaseModel):
    """
    Channel-agnostic inbound message.

    This model can represent messages from any supported channel,
    abstracting away channel-specific details.
    """

    message_id: str = Field(..., description="Unique message ID from source channel")
    user_id: str = Field(..., description="User identifier (phone number, user ID, etc.)")
    channel: ChannelType = Field(..., description="Source channel")
    message_type: MessageType = Field(default=MessageType.TEXT)
    text: str | None = Field(None, description="Text content")
    media_url: str | None = Field(None, description="URL or ID for media content")
    mime_type: str | None = Field(None, description="MIME type if media present")
    reply_to_id: str | None = Field(None, description="ID of message being replied to")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    retry_count: int = Field(default=0)
    channel_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Channel-specific data (flow data, etc.)",
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    @classmethod
    def from_whatsapp(cls, wa_message: Any) -> "ChannelMessage":
        """
        Create ChannelMessage from a WhatsAppMessage.

        This factory method handles the conversion from WhatsApp-specific
        fields to the generic model.
        """
        return cls(
            message_id=wa_message.message_id,
            user_id=wa_message.from_number,
            channel=ChannelType.WHATSAPP,
            message_type=MessageType(wa_message.message_type.value),
            text=wa_message.text,
            media_url=wa_message.media_id,
            mime_type=wa_message.mime_type,
            reply_to_id=wa_message.quoted_message_id,
            timestamp=wa_message.timestamp,
            priority=MessagePriority(wa_message.priority.value),
            retry_count=wa_message.retry_count,
            channel_metadata={"flow_data": wa_message.flow_data} if wa_message.flow_data else {},
        )
