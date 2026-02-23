"""Webhooks package - split by provider."""

from .mono import router as mono_router
from .telegram import telegram_router
from .whatsapp import flows_router, message_router

__all__ = ["mono_router", "message_router", "flows_router", "telegram_router"]
