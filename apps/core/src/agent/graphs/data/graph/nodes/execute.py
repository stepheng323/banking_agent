"""Execute node - performs the data purchase."""

from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_phone

logger = get_logger(__name__)


async def execute_node(
    state: DataPurchaseState,
) -> dict:
    """
    Execute node for data purchase.

    Since authorization now handles the actual execution enqueueing,
    this node simply formats the immediate response to the user.
    """
    selected_plan = state.get("selected_plan") or state.get("suggested_plan")
    target_phone = state.get("target_phone", "")
    network = state.get("network", "")

    if not selected_plan:
        return {
            "flow_state": "error",
            "error": "No plan selected",
            "response": "Something went wrong. Please start over.",
        }

    normalized_phone = normalize_phone(target_phone)

    logger.info(
        "execute_node_passed",
        plan=selected_plan.item_code,
        network=network,
        phone=normalized_phone,
        status=state.get("data_status"),
    )

    # We don't call provider here. Authorization enqueues it for the worker.
    # We just return completed state. The worker sends the final notification.

    return {
        "flow_state": "completed",
        "response": "",  # Empty response, let async worker notify
    }
