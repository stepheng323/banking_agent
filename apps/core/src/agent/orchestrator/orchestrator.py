"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.transfer import TransferService

from apps.core.src.agent.orchestrator.config import OrchestratorDependencies
from apps.core.src.agent.orchestrator.graph_handler import OrchestratorGraphHandler
from apps.core.src.agent.orchestrator.models.message_context import MessageContext
from shared.services import OrchestratorContextManager, OrchestratorTaskPlanner
from shared.utils.async_helpers import create_background_task


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(self, deps: OrchestratorDependencies) -> None:
        self.deps = deps
        self.message_type = "text"

        self.context_manager = OrchestratorContextManager(deps.user_repo, deps.beneficiary_repo)
        self.task_planner = OrchestratorTaskPlanner(deps.llm, deps.task_queue_service)

        self.orchestrator_handler = OrchestratorGraphHandler(
            task_planner=self.task_planner,
            transfer_service=self.deps.transfer_service,
            airtime_service=self.deps.airtime_service,
            query_service=self.deps.query_service,
            data_service=self.deps.data_service,
            account_service=self.deps.account_service,
            support_service=self.deps.support_service,
            faq_service=self.deps.faq_service,
            user_cache=self.deps.user_cache,
            redis_client=self.deps.redis_client,
            whatsapp_client=self.deps.whatsapp_client,
            user_repo=self.deps.user_repo,
            beneficiary_repo=self.deps.beneficiary_repo,
            account_repo=self.deps.account_repo,
            banking_provider=self.deps.banking_provider,
            queue=self.deps.queue,
        )

    @property
    def transfer(self) -> "TransferService":
        """Get transfer service."""
        return self.deps.transfer_service

    async def resume_transaction(self, phone_number: str, flow_type: str, pin_verified: bool) -> str | None:
        """Resume a transaction after an external event (like PIN verification)."""
        payload = {"pin_verified": pin_verified, "flow_type": flow_type}
        return await self.orchestrator_handler.resume_flow(phone_number=phone_number, payload=payload)

    async def invoke(
        self,
        phone_number: str,
        text: str,
        message_id: str,
        message_type: str = "text",
        media_id: str | None = None,
        quoted_message_id: str | None = None,
    ) -> str:
        """Invoke the orchestrator with a user message."""
        self.message_type = message_type

        # 1. Media Processing
        if self.message_type == "audio" and media_id:
            raw_text = await self.deps.media_service.process_audio(media_id)
            if raw_text:
                text = raw_text

        image_data = None
        if self.message_type == "image" and media_id:
            image_data = await self.deps.media_service.get_image_data(media_id)

        # 2. Context Loading (Lightweight)
        # We assume the Graph manages its own state via Checkpointer.
        # But we might want to ensure user exists or load profile into cache?
        # For now, we rely on the services/adapters to fetch what they need.

        # 3. Build Context (Legacy structure, still used by Handler signature)
        context = MessageContext(
            phone_number=phone_number,
            text=text,
            message_id=message_id,
            image_data=image_data,
            quoted_message_id=quoted_message_id,
        )

        response_text = await self.orchestrator_handler.invoke(context)
        final_response = response_text or "I'm sorry, I'm having trouble processing that right now."

        create_background_task(self.context_manager.add_conversation_turn(phone_number, "user", text))
        create_background_task(self.context_manager.add_conversation_turn(phone_number, "assistant", final_response))

        return final_response
