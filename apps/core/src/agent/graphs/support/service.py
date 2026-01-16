"""Support service facade using LangGraph."""

from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import Runnable
from sqlalchemy.orm import Session

from apps.core.src.agent.graphs.interfaces import IAgentService
from apps.core.src.agent.graphs.support.graph.graph import SupportFlowGraph
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.support_ticket_repository import SupportTicketRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.services.ticket_service import TicketService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class SupportService(IAgentService):
    """Support service facade using LangGraph."""

    def __init__(
        self,
        llm: Runnable,
        transaction_repo: TransactionRepository,
        actionable_message_repo: ActionableMessageRepository,
        redis_client: redis.Redis,
        db_session: Session | None = None,
    ):
        self.graph = SupportFlowGraph(
            llm=llm,
            transaction_repo=transaction_repo,
            actionable_message_repo=actionable_message_repo,
            redis_client=redis_client,
            db_session=db_session,
        )
        
        self._ticket_service = None
        if db_session:
            ticket_repo = SupportTicketRepository(db_session)
            self._ticket_service = TicketService(ticket_repo)

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the support flow."""
        user_id = classification_result.get("user_id", "") if classification_result else ""
        quoted_message_id = quoted_data.get("wa_message_id") if quoted_data else None
        transaction = quoted_data.get("transaction") if quoted_data else None
        
        result = await self.graph.run(
            phone_number=phone,
            message=text,
            user_id=user_id,
            quoted_message_id=quoted_message_id,
            transaction=transaction,
        )
        
        return result or ""

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear support flow checkpoint for a user."""
        try:
            await self.graph.clear_checkpoint(phone_number)
        except Exception as e:
            logger.error("support_checkpoint_clear_error", phone=phone_number, error=str(e))
