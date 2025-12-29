"""Suggest node - provides smart plan suggestions."""

from apps.core.src.agent.sub_agents.data.graph.state import DataPurchaseState
from apps.core.src.agent.sub_agents.data.service import DataPlanService
from shared.formatters.data import format_data_plan_suggestion
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def suggest_node(
    state: DataPurchaseState,
    plan_service: DataPlanService,
) -> dict:
    """
    Suggest a data plan based on user history or budget.

    Priority:
    1. Last successful purchase (same network, last 30 days)
    2. Budget match (if user specified amount)
    3. Popular/default plan for network
    """
    network = state.get("network", "")
    budget = state.get("budget")
    last_purchase = state.get("last_data_purchase")
    attempts = state.get("suggestion_attempts", 0)

    all_plans = await plan_service.get_plans(network)

    if not all_plans:
        return {
            "flow_state": "error",
            "error": f"No data plans available for {network}",
            "response": f"Sorry, I couldn't find any data plans for {network} right now. Please try again later.",
        }

    suggested_plan = None
    reason = "default"

    if attempts == 0 and last_purchase:
        last_plan_code = last_purchase.get("plan_code")
        for plan in all_plans:
            if plan.item_code == last_plan_code:
                suggested_plan = plan
                reason = "repeat"
                break

    if not suggested_plan and budget:
        suggested_plan = plan_service.get_best_plan_for_budget(all_plans, budget)
        if suggested_plan:
            reason = "budget"

    if not suggested_plan:
        mid_range = [p for p in all_plans if 1000 <= p.amount <= 3000]
        if mid_range:
            monthly = [p for p in mid_range if p.validity_days and p.validity_days >= 28]
            suggested_plan = monthly[0] if monthly else mid_range[0]
        else:
            suggested_plan = all_plans[0]
        reason = "default"

    response = format_data_plan_suggestion(
        plan_name=suggested_plan.name,
        network=network,
        amount=suggested_plan.amount,
        validity_days=suggested_plan.validity_days,
        size_gb=suggested_plan.size_gb,
        reason=reason,
    )

    logger.info(
        "suggest_complete",
        network=network,
        plan=suggested_plan.name,
        reason=reason,
    )

    return {
        "suggested_plan": suggested_plan,
        "all_plans": all_plans,
        "flow_state": "awaiting_confirmation",
        "suggestion_attempts": attempts + 1,
        "response": response,
    }
