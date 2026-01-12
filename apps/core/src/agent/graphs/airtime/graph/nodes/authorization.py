"""Authorization node for airtime purchase flow using shared base."""

from typing import Any, cast

from apps.core.src.agent.graphs.__shared__.authorization import AuthorizationBase
from apps.core.src.agent.graphs.__shared__.response import ResponseIntent
from apps.core.src.agent.graphs.airtime.state import AirtimeState
from shared.cache.redis_client import Redis
from shared.queue.redis_queue import RedisQueue
from shared.services.auth import AuthorizationService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AirtimeAuthorization(AuthorizationBase[AirtimeState]):
    """Airtime-specific authorization extending shared base."""

    def get_flow_type(self) -> str:
        return "airtime"

    def get_transaction_type(self) -> str:
        return "airtime"

    def get_status_field(self) -> str:
        return "airtime_status"

    def get_pending_data_key(self, phone_number: str) -> str | None:
        return f"user:{phone_number}:pending_airtime"

    def get_error_intent(self) -> ResponseIntent:
        return ResponseIntent.AIRTIME_FAILED

    def build_transaction_params(self, state: AirtimeState, user_id: str) -> dict[str, Any]:
        """Build airtime transaction parameters from state or Redis."""
        amount = state.get("amount")
        recipient_phone = state.get("recipient_phone", "")
        network = state.get("network", "")
        source = state.get("selected_source_account", {})

        return {
            "amount": float(amount) if amount else 0,
            "currency": "NGN",
            "source_account_id": source.get("id"),
            "source_account_number": source.get("account_number", ""),
            "source_bank_name": source.get("bank_name", ""),
            "recipient_account_number": recipient_phone,
            "recipient_bank_code": network,
            "recipient_bank_name": network,
            "recipient_name": recipient_phone,
            "narration": f"Airtime {network} {recipient_phone}",
        }

    async def enqueue_for_execution(
        self,
        state: AirtimeState,
        transaction_id: str,
        transaction_params: dict[str, Any],
        user_id: str,
    ) -> None:
        """Enqueue airtime purchase for execution."""
        idem_key = state.get("idempotency_key")
        phone_number = state.get("phone_number")

        airtime_data = {
            "amount": transaction_params["amount"],
            "recipient": {
                "phone": transaction_params["recipient_account_number"],
                "network": transaction_params["recipient_bank_code"],
                "name": transaction_params["recipient_name"],
            },
            "source": {
                "id": transaction_params.get("source_account_id"),
                "account_number": transaction_params.get("source_account_number", ""),
                "bank_name": transaction_params.get("source_bank_name", ""),
            },
            "narration": transaction_params["narration"],
        }

        await self.queue.enqueue_simple(
            queue_name="banking:transactions",
            message={
                "type": "execute_airtime",
                "phone_number": phone_number,
                "idempotency_key": idem_key,
                "airtime_data": airtime_data,
                "transaction_id": transaction_id,
            },
        )

    def build_success_response(self, state: AirtimeState, transaction_id: str, retry_count: int) -> AirtimeState:
        """Build success response - empty since executor sends notification."""
        return cast(
            AirtimeState,
            {
                **state,
                "response": "",
                "flow_state": "completed",
                "airtime_status": "authorized",
                "pin_verified": True,
                "pin_retry_count": retry_count,
            },
        )


async def authorize_transaction(
    state: AirtimeState,
    redis_client: Redis,
    queue: RedisQueue,
    authorization_service: AuthorizationService,
) -> AirtimeState:
    """Authorize transaction after PIN verification - node function for graph."""
    auth = AirtimeAuthorization(redis_client, queue, authorization_service)
    return await auth.authorize(state)
