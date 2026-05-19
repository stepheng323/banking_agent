"""Telegram update adapter — parses Telegram webhook payloads."""

from typing import Any

from pydantic import BaseModel, Field


class ParsedTelegramMessage(BaseModel):
    """Standardized representation of a Telegram update."""

    message_id: str | None = None
    chat_id: str = ""
    text: str = ""
    type: str = ""  # "text" | "photo" | "audio" | "callback_query" | "web_app_data"

    photo_file_id: str | None = None
    audio_file_id: str | None = None

    callback_query_id: str | None = None
    callback_data: str | None = None

    web_app_data: str | None = None

    quoted_message_id: str | None = None

    contact_phone_number: str | None = None
    contact_user_id: str | None = None

    from_user_id: str | None = None
    from_username: str | None = None
    from_first_name: str | None = None

    raw: dict[str, Any] = Field(default_factory=dict)


def parse_update(update: dict[str, Any]) -> ParsedTelegramMessage | None:
    """Parse a Telegram Update object into a ParsedTelegramMessage.

    Handles:
    - Regular messages (text, photo, audio)
    - Callback queries (inline button presses)
    - web_app_data (Mini App responses e.g. PIN entry)

    Returns:
        ParsedTelegramMessage or None if the update is not actionable.
    """
    callback_query: dict[str, Any] | None = update.get("callback_query")
    if callback_query:
        message: dict[str, Any] = callback_query.get("message", {})
        chat: dict[str, Any] = message.get("chat", {})
        from_user: dict[str, Any] = callback_query.get("from", {})
        return ParsedTelegramMessage(
            message_id=str(message.get("message_id", "")),
            chat_id=str(chat.get("id", "")),
            text=callback_query.get("data", ""),
            type="callback_query",
            callback_query_id=callback_query.get("id"),
            callback_data=callback_query.get("data"),
            from_user_id=str(from_user.get("id", "")),
            from_username=from_user.get("username"),
            from_first_name=from_user.get("first_name"),
            raw=update,
        )

    msg: dict[str, Any] | None = update.get("message")
    if not msg:
        return None

    chat = msg.get("chat", {})
    from_user = msg.get("from", {})
    chat_id = str(chat.get("id", ""))

    reply_to = msg.get("reply_to_message")
    quoted_message_id = str(reply_to.get("message_id")) if reply_to and reply_to.get("message_id") else None

    web_app: dict[str, Any] | None = msg.get("web_app_data")
    if web_app:
        return ParsedTelegramMessage(
            message_id=str(msg.get("message_id", "")),
            chat_id=chat_id,
            text="",
            type="web_app_data",
            web_app_data=web_app.get("data"),
            quoted_message_id=quoted_message_id,
            from_user_id=str(from_user.get("id", "")),
            from_username=from_user.get("username"),
            from_first_name=from_user.get("first_name"),
            raw=update,
        )

    photos: list[dict[str, Any]] = msg.get("photo", [])
    if photos:
        best_photo = photos[-1]
        return ParsedTelegramMessage(
            message_id=str(msg.get("message_id", "")),
            chat_id=chat_id,
            text=msg.get("caption", ""),
            type="photo",
            photo_file_id=best_photo.get("file_id"),
            quoted_message_id=quoted_message_id,
            from_user_id=str(from_user.get("id", "")),
            from_username=from_user.get("username"),
            from_first_name=from_user.get("first_name"),
            raw=update,
        )

    audio: dict[str, Any] | None = msg.get("audio") or msg.get("voice")
    if audio:
        return ParsedTelegramMessage(
            message_id=str(msg.get("message_id", "")),
            chat_id=chat_id,
            text="",
            type="audio",
            audio_file_id=audio.get("file_id"),
            quoted_message_id=quoted_message_id,
            from_user_id=str(from_user.get("id", "")),
            from_username=from_user.get("username"),
            from_first_name=from_user.get("first_name"),
            raw=update,
        )

    contact_obj: dict[str, Any] | None = msg.get("contact")
    if contact_obj:
        return ParsedTelegramMessage(
            message_id=str(msg.get("message_id", "")),
            chat_id=chat_id,
            text="",
            type="contact",
            contact_phone_number=contact_obj.get("phone_number"),
            contact_user_id=str(contact_obj.get("user_id", "")) if contact_obj.get("user_id") else None,
            quoted_message_id=quoted_message_id,
            from_user_id=str(from_user.get("id", "")),
            from_username=from_user.get("username"),
            from_first_name=from_user.get("first_name"),
            raw=update,
        )

    text_content: str = msg.get("text", "")
    if text_content:
        return ParsedTelegramMessage(
            message_id=str(msg.get("message_id", "")),
            chat_id=chat_id,
            text=text_content,
            type="text",
            quoted_message_id=quoted_message_id,
            from_user_id=str(from_user.get("id", "")),
            from_username=from_user.get("username"),
            from_first_name=from_user.get("first_name"),
            raw=update,
        )

    return None
