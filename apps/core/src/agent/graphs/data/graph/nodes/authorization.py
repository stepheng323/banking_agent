"""Authorization node for data purchase flow using shared base."""

from typing import Any, cast

from apps.core.src.agent.graphs.__shared__.authorization import AuthorizationBase
from apps.core.src.agent.graphs.__shared__.response import ResponseIntent
from apps.core.src.agent.graphs.data.graph.state import DataPurchaseState
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from shared.services.auth import AuthorizationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class DataAuthorization(AuthorizationBase[DataPurchaseState]):
    """Data purchase authorization extending shared base."""

    def get_flow_type(self) -> str:
        return "data"

    def get_transaction_type(self) -> str:
        return "data"

    def get_status_field(self) -> str:
        return "data_status"

    def get_pending_data_key(self, phone_number: str) -> str | None:
        return f"user:{phone_number}:pending_data"

    def get_error_intent(self) -> ResponseIntent:
        return ResponseIntent.DATA_FAILED

    def build_transaction_params(self, state: DataPurchaseState, user_id: str) -> dict[str, Any]:
        """Build data purchase transaction parameters."""
        selected_plan = state.get("selected_plan") or state.get("suggested_plan")

        if not selected_plan:
            raise ValueError("No data plan selected")

        target_phone = state.get("target_phone", "")
        network = state.get("network", "")
        plan_name = selected_plan.name
        plan_code = selected_plan.item_code

        return {
            "amount": float(selected_plan.amount),
            "currency": "NGN",
            "recipient_account_number": target_phone,
            "recipient_bank_code": network,
            "recipient_bank_name": network,
            "recipient_name": plan_name,
            "narration": f"Data: {plan_name} ({plan_code})",
        }

    async def enqueue_for_execution(
        self,
        state: DataPurchaseState,
        transaction_id: str,
        transaction_params: dict[str, Any],
        user_id: str,
    ) -> None:
        """Enqueue data purchase for execution."""
        idem_key = state.get("idempotency_key")
        phone_number = state.get("phone_number")
        selected_plan = state.get("selected_plan") or state.get("suggested_plan")

        data_purchase = {
            "plan_code": selected_plan.item_code if selected_plan else "",
            "plan_name": transaction_params["recipient_name"],
            "amount": transaction_params["amount"],
            "target_phone": transaction_params["recipient_account_number"],
            "network": transaction_params["recipient_bank_code"],
            "source": state.get("source", "self"),
        }

        await self.queue.enqueue_simple(
            queue_name="banking:transactions",
            message={
                "type": "execute_data",
                "phone_number": phone_number,
                "idempotency_key": idem_key,
                "data_purchase": data_purchase,
                "transaction_id": transaction_id,
            },
        )

    def build_success_response(
        self, state: DataPurchaseState, transaction_id: str, retry_count: int
    ) -> DataPurchaseState:
        """Build success response - empty since executor sends notification."""
        return cast(
            DataPurchaseState,
            {
                **state,
                "response": "",
                "flow_state": "completed",
                "data_status": "authorized",
                "pin_verified": True,
                "pin_retry_count": retry_count,
            },
        )


async def authorize_transaction(
    state: DataPurchaseState,
    redis_client: Redis,
    queue: RedisQueue,
    authorization_service: AuthorizationService | None = None,
) -> DataPurchaseState:
    """Authorize data purchase after PIN verification - node function for graph."""
    auth = DataAuthorization(redis_client, queue, authorization_service)
    return await auth.authorize(state)
