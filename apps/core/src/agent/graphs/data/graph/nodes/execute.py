"""Execute node - performs the data purchase."""

from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState
from shared.clients.abstractions.bill import BillPaymentProvider
from shared.formatters.data import format_data_failure_message, format_data_success_message
from shared.utils.logging import get_logger
from shared.utils.network_utils import normalize_phone

logger = get_logger(__name__)


async def execute_node(
    state: DataPurchaseState,
    bill_provider: BillPaymentProvider,
) -> dict:
    """
    Execute the data purchase.

    Uses the selected plan and target phone to make the purchase.
    """
    selected_plan = state.get("selected_plan") or state.get("suggested_plan")
    target_phone = state.get("target_phone", "")
    network = state.get("network", "")
    source = state.get("source", "self")

    if not selected_plan:
        return {
            "flow_state": "error",
            "error": "No plan selected",
            "response": "Something went wrong. Please start over.",
        }

    normalized_phone = normalize_phone(target_phone)

    logger.info(
        "execute_data_purchase",
        plan=selected_plan.item_code,
        network=network,
        phone=normalized_phone,
        amount=selected_plan.amount,
    )

    result = await bill_provider.purchase_data(
        plan_code=selected_plan.item_code,
        recipient_phone=normalized_phone,
        network=network,
    )

    if result.get("success"):
        response = format_data_success_message(
            plan_name=selected_plan.name,
            amount=selected_plan.amount,
            target_phone=target_phone,
            source=source,
        )

        logger.info(
            "data_purchase_success",
            transaction_id=result.get("transaction_id"),
            plan=selected_plan.name,
        )

        return {
            "flow_state": "completed",
            "response": response,
        }
    else:
        error = result.get("error", "Unknown error")
        response = format_data_failure_message(error)

        logger.warning(
            "data_purchase_failed",
            error=error,
            plan=selected_plan.name,
        )

        return {
            "flow_state": "error",
            "error": error,
            "response": response,
        }
