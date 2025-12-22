"""Webhooks package - split by provider."""
from apps.gateway.api.webhooks.mono import router as mono_router
from apps.gateway.api.webhooks.whatsapp import router as whatsapp_router
from apps.gateway.api.webhooks.whatsapp import flows_router

__all__ = ["mono_router", "whatsapp_router", "flows_router"]

