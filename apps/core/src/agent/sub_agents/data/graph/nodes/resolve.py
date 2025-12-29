"""Resolve node - determines target phone and network."""

from apps.core.src.agent.sub_agents.data.graph.state import DataPurchaseState
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_phone, resolve_network_from_phone

logger = get_logger(__name__)


async def resolve_node(state: DataPurchaseState) -> dict:
    """
    Resolve target phone and network.

    Determines:
    - Is this for self or another number?
    - What network is the target phone on?
    """
    phone_number = state.get("phone_number", "")
    message = state.get("message", "").lower()
    user_context = state.get("user_context", {})

    target_phone = phone_number
    source = "self"

    import re

    phone_pattern = re.search(r"for\s+(0\d{10}|\+?234\d{10})", message)
    if phone_pattern:
        target_phone = normalize_phone(phone_pattern.group(1))
        source = "other"

    network = resolve_network_from_phone(target_phone)

    if not network:
        profile = user_context.get("profile", {})
        if profile.get("phone_number") == target_phone:
            pass

        if not network:
            return {
                "flow_state": "error",
                "error": "Could not determine network. Please specify the network (e.g., MTN, Airtel).",
                "response": "I couldn't detect the network for that number. Which network is it? MTN, Airtel, Glo, or 9mobile?",
            }

    logger.info(
        "resolve_complete",
        target_phone=target_phone,
        source=source,
        network=network,
    )

    return {
        "target_phone": target_phone,
        "source": source,
        "network": network,
        "flow_state": "suggesting",
    }
