"""
Flow Webhook for handling WhatsApp Flow data exchange.
This package contains the refactored flow webhook handlers.
"""

from apps.gateway.api.flows.router import router

__all__ = ["router"]
