"""Query Worker.

Stateless worker for Query tasks.
Executes: Extract headers -> Parse/Continuity -> Execute -> Format.
Manages session persistence via Redis.
"""

from types import SimpleNamespace
from typing import Any

from langchain_core.runnables import Runnable

from apps.core.src.agent.graphs.query.nodes.execution import ExecutionStep
from apps.core.src.agent.graphs.query.nodes.extraction import ExtractionStep
from apps.core.src.agent.graphs.query.pipeline import QueryPipeline
from apps.core.src.agent.graphs.query.session import QuerySessionManager
from apps.core.src.agent.orchestrator.models.domain import TransactionResult
from shared.clients.abstractions.banking import BankingDataProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class QueryWorker:
    """Worker for executing query tasks."""

    def __init__(
        self,
        llm: Runnable,
        banking_provider: BankingDataProvider,
        session_manager: QuerySessionManager,
    ):
        self.llm = llm
        self.banking_provider = banking_provider
        self.session_manager = session_manager

        # Initialize pipeline steps once
        self.extractor = ExtractionStep(llm)
        self.executor = ExecutionStep()
        self.pipeline = QueryPipeline([self.extractor, self.executor])

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransactionResult:
        """Run the query pipeline."""

        # 1. Load Session
        phone_number = context.get("phone_number")
        session_key = f"query:session:{phone_number}"
        query_session = await self.session_manager.load(session_key) or {}

        # 2. Build Initial State
        state = {
            "message": payload.get("message", ""),
            "phone_number": phone_number,
            "account_id": payload.get("account_id"),  # Might come from previous context or current
            "account_ids": payload.get("account_ids"),
            "accounts": context.get("accounts", []),
            "query_session": query_session,
            "flow_state": "parsing",
            # Default pagination params
            "current_page": query_session.get("current_page", 0),
            "page_size": 5,
        }

        # 3. Setup Worker Context
        worker_context = SimpleNamespace(
            banking_provider=self.banking_provider,
            user_id=context.get("user_id"),
        )

        # 4. Run Pipeline
        try:
            result = await self.pipeline.run(state, worker_context)

            # 5. Handle Session Persistence
            if result.outcome.is_successful and result.patch:
                # Merge patch for saving
                final_state = {**state, **result.patch}

                # Determine if session should remain active
                # Logic: If we have results, session is active. If errors or specific end intent, close.
                # The 'session_active' flag might be set by ExecutionStep.
                session_active = final_state.get("session_active", False)

                if session_active:
                    final_state["timestamp"] = __import__("time").time()
                    await self.session_manager.save(session_key, final_state)
                else:
                    await self.session_manager.clear(session_key)

            return result

        except Exception as e:
            logger.error("query_worker_error", error=str(e), exc_info=True)
            return TransactionResult(
                outcome=__import__(
                    "apps.core.src.agent.orchestrator.models.domain", fromlist=["TransactionOutcome"]
                ).TransactionOutcome.FAILED,
                error="Sorry, I encountered an error providing that information.",
            )
