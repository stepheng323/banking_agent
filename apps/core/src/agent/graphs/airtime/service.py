"""Airtime purchase service facade using LangGraph."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.graphs.airtime.graph import AirtimeFlowGraph
from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.utils.logging import get_logger

from ..interfaces import FlowCompletionCallback, IAgentService

logger = get_logger(__name__)


class AirtimeService(IAgentService):
    """Airtime purchase service facade using LangGraph."""

    def __init__(
        self,
        llm: ChatOpenAI | None,
        user_cache: UserDataCache,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        queue: RedisQueue,
        actionable_message_repo: ActionableMessageRepository | None = None,
        completion_callback: FlowCompletionCallback | None = None,
    ) -> None:
        self.extractor = AirtimeEntityExtractor(llm)
        self.graph = AirtimeFlowGraph(
            user_cache=user_cache,
            account_repo=account_repo,
            beneficiary_repo=beneficiary_repo,
            whatsapp_client=whatsapp_client,
            extractor=self.extractor,
            queue=queue,
            actionable_message_repo=actionable_message_repo,
            completion_callback=completion_callback,
        )
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.beneficiary_repo = beneficiary_repo

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the airtime purchase flow using LangGraph."""
        return await self.graph.run(
            phone, text, "", classification_result, image_data=image_data, quoted_data=quoted_data
        )

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear airtime flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            logger.error("airtime_checkpoint_clear_error", phone=phone_number, error=str(e), exc_info=True)

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, extra_param: Any = None
    ) -> str:
        """Resume airtime purchase flow after PIN verification."""
        return await self.graph.resume_after_pin_verification(phone_number, pin_verified, extra_param)

    async def get_last_state(self, phone: str) -> dict[str, Any] | None:
        """Get the last workflow state (checkpoint)."""
        return await self.graph.get_checkpoint_state(phone)

    async def preflight(self, phone: str, text: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Prepare airtime task by enriching parameters and checking readiness.
        """
        from apps.core.src.agent.graphs.airtime.graph.nodes.context import load_user_context
        from apps.core.src.agent.graphs.airtime.graph.nodes.validation import (
            validate_amount,
            validate_network,
            validate_phone,
        )
        from apps.core.src.agent.graphs.airtime.state import AirtimeState

        # 1. Initialize State
        state: AirtimeState = {
            "phone_number": phone,
            "message": text,
            "amount": params.get("amount"),
            "recipient_phone": params.get("recipient_phone") or params.get("phone"),
            "network": params.get("network"),
            "flow_state": "preflight",
            "validation_errors": [],
        }

        # 2. Load User Context (Account Resolution)
        state = await load_user_context(
            state,
            user_cache=self.user_cache,
            account_repo=self.account_repo,
            beneficiary_repo=self.beneficiary_repo,
        )

        if state.get("flow_state") == "error":
            return {
                "ready": False,
                "question": state.get("response"),
                "missing_fields": ["account_validation"],
            }

        # 3. Validate Fields
        state = await validate_amount(state)
        state = await validate_phone(state)
        state = await validate_network(state)

        # 4. Check for blocking errors/questions
        if state.get("flow_state") in ["collecting_amount", "collecting_phone", "error"]:
            missing = []
            if not state.get("amount"):
                missing.append("amount")
            if not state.get("recipient_phone"):
                missing.append("recipient_phone")
            if not state.get("network"):
                missing.append("network")

            return {
                "ready": False,
                "question": state.get("response"),
                "missing_fields": missing,
                "enriched_params": {
                    "amount": state.get("amount"),
                    "recipient_phone": state.get("recipient_phone"),
                    "network": state.get("network"),
                    "source_account_id": (state.get("accounts") or [{}])[0].get("account_id")
                    if state.get("accounts")
                    else None,
                },
            }

        # 5. Success - Return Enriched Params
        accounts = state.get("accounts", [])
        source_account = accounts[0] if accounts else {}

        return {
            "ready": True,
            "enriched_params": {
                "amount": state.get("amount"),
                "recipient_phone": state.get("recipient_phone"),
                "network": state.get("network"),
                "source_account_id": source_account.get("account_id"),
                "source_account_number": source_account.get("account_number"),
                "source_bank_name": source_account.get("bank_name"),
            },
        }

    def set_completion_callback(self, callback: FlowCompletionCallback | None) -> None:
        """Set the completion callback for the airtime flow."""
        self.graph.completion_callback = callback
