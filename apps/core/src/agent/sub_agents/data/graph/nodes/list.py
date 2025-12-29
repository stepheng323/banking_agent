"""List node - shows paginated data plans."""

from apps.core.src.agent.sub_agents.data.graph.state import DataPurchaseState
from shared.formatters.data import format_data_plan_list
from shared.utils.logging import get_logger

logger = get_logger(__name__)

PLANS_PER_PAGE = 5


async def list_node(state: DataPurchaseState, page: int = 0) -> dict:
    """
    Show paginated list of data plans.

    Only triggered when:
    - User explicitly asks to see plans
    - User rejects 2+ suggestions
    """
    network = state.get("network", "")
    all_plans = state.get("all_plans", [])

    if not all_plans:
        return {
            "flow_state": "error",
            "error": f"No plans available for {network}",
            "response": f"Sorry, I couldn't find any data plans for {network}.",
        }

    sorted_plans = sorted(all_plans, key=lambda p: p.amount)

    start = page * PLANS_PER_PAGE
    end = start + PLANS_PER_PAGE
    page_plans = sorted_plans[start:end]
    has_more = end < len(sorted_plans)

    plans_data = [
        {
            "name": p.name,
            "size_gb": p.size_gb,
            "amount": p.amount,
            "validity_days": p.validity_days,
        }
        for p in page_plans
    ]

    response = format_data_plan_list(plans_data, network, has_more)

    logger.info(
        "list_plans",
        network=network,
        page=page,
        total_plans=len(all_plans),
    )

    return {
        "flow_state": "listing",
        "response": response,
    }
