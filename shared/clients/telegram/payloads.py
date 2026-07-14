"""Telegram Bot API payload builders."""

from typing import Any

from shared.clients.telegram.formatting import format_telegram_html


def build_send_message_payload(
    *,
    to: str,
    text: str,
    message_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": to,
        "text": format_telegram_html(text),
        "parse_mode": "HTML",
    }
    if message_id:
        payload["reply_to_message_id"] = message_id
    return payload


def build_interactive_message_payload(
    *,
    to: str,
    body_text: str,
    options: list[dict[str, str]],
    header: str = "",
    footer: str = "",
) -> dict[str, Any]:
    parts: list[str] = []
    if header:
        parts.append(f"*{header}*")
    parts.append(body_text)
    if footer:
        parts.append(f"_{footer}_")

    return {
        "chat_id": to,
        "text": format_telegram_html("\n\n".join(parts)),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": build_inline_keyboard_rows(options)},
    }


def build_inline_keyboard_rows(options: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    return [
        [{"text": opt.get("title", opt.get("id", "Option")), "callback_data": opt.get("id", "")}] for opt in options
    ]


def build_image_payload(*, to: str, image_url: str, caption: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": to,
        "photo": image_url,
    }
    if caption:
        payload["caption"] = caption
    return payload


def build_image_upload_payload(
    *,
    to: str,
    data: bytes,
    caption: str = "",
    mime_type: str = "image/png",
) -> tuple[dict[str, Any], dict[str, Any]]:
    extension = mime_type.split("/")[-1]
    form_data: dict[str, Any] = {"chat_id": to}
    if caption:
        form_data["caption"] = caption
    return form_data, {"photo": (f"image.{extension}", data, mime_type)}


def build_document_upload_payload(
    *,
    to: str,
    data: bytes,
    filename: str,
    caption: str = "",
    mime_type: str = "application/pdf",
) -> tuple[dict[str, Any], dict[str, Any]]:
    form_data: dict[str, Any] = {"chat_id": to}
    if caption:
        form_data["caption"] = caption
    return form_data, {"document": (filename, data, mime_type)}


def build_typing_indicator_payload(*, chat_id: str) -> dict[str, Any]:
    return {"chat_id": chat_id, "action": "typing"}


def build_answer_callback_query_payload(*, callback_query_id: str, text: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return payload


def build_authorized_reply_markup_payload(*, chat_id: str, message_id: str | int) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": [[{"text": "✓ Authorized", "callback_data": "auth:done"}]]},
    }


def build_remove_inline_keyboard_payload(*, chat_id: str, message_id: str | int) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "message_id": message_id,
    }
