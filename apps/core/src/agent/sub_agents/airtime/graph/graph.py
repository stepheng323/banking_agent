"""LangGraph graph for airtime purchase flow."""

import os
from typing import Optional, cast, TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.src.agent.services import FlowCompletionCallback
import asyncio

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.account_repository import AccountRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.cache.redis_client import RedisClient
from shared.queue.redis_queue import RedisQueue
from shared.config.settings import settings
from apps.core.src.agent.sub_agents.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState

from .builder import build_graph
from .state import create_initial_state, update_conversation_state


class AirtimeFlowGraph:
    """LangGraph-based airtime purchase flow."""

    def __init__(
        self,
        user_cache: UserDataCache,
        account_repo: AccountRepository,
        beneficiary_repo: BeneficiaryRepository,
        whatsapp_client: WhatsAppClient,
        extractor: AirtimeEntityExtractor,
        queue: RedisQueue,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ):
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.beneficiary_repo = beneficiary_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.completion_callback = completion_callback
        self.redis_client = RedisClient.get_client()
        self.queue = queue

        self.graph = None
        self._checkpointer_cm = None
        self._checkpointer = None
        self._checkpointer_setup = False

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear airtime flow checkpoint for a user."""
        from shared.utils.logging import get_logger
        logger = get_logger(__name__)
        try:
            await self._ensure_checkpointer()
            config: RunnableConfig = {
                "configurable": {"thread_id": f"airtime:{phone_number}"}
            }
            if self._checkpointer:
                thread_id = config["configurable"]["thread_id"]
                await self._checkpointer.adelete_thread(thread_id)
                logger.info(f"Cleared airtime checkpoint for {phone_number}")
            else:
                logger.warning(f"Checkpointer not initialized, cannot clear airtime checkpoint for {phone_number}")
        except Exception as e:
            logger.error(f"Error clearing airtime checkpoint: {e}")

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self._checkpointer = AsyncRedisSaver(redis_url=settings.redis_url)
            await self._checkpointer.asetup()
            self._checkpointer_setup = True

        if self.graph is None:
            self.graph = build_graph(
                extractor=self.extractor,
                user_cache=self.user_cache,
                account_repo=self.account_repo,
                beneficiary_repo=self.beneficiary_repo,
                matcher=self.matcher,
                whatsapp_client=self.whatsapp_client,
                redis_client=self.redis_client,
                queue=self.queue,
            ).compile(
                checkpointer=self._checkpointer,
                interrupt_before=["authorize"],  # Pause before authorize, wait for PIN
            )

    async def run(self, phone_number: str, message: str, message_id: str, classification_result: Optional[dict] = None) -> str:
        """Run the airtime purchase flow graph."""
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"airtime:{phone_number}",
            }
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        # Check if this is a flow resume - just replay the last question
        is_flow_resume = (
            classification_result and 
            classification_result.get("complexity_reason") == "Flow resume after interrupt"
        )
        if is_flow_resume:
            import json
            paused_flow_key = f"user:{phone_number}:paused_flow"
            paused_flow_data = await self.redis_client.get(paused_flow_key)
            if paused_flow_data:
                paused = json.loads(paused_flow_data)
                saved_response = paused.get("last_response")
                flow_summary = paused.get("flow_summary", {})
                
                if saved_response:
                    # Add contextual prefix for airtime
                    amount = flow_summary.get("amount")
                    recipient_phone = flow_summary.get("recipient_phone", "")
                    
                    if amount and recipient_phone:
                        context_prefix = f"Continuing your ₦{amount:,.0f} airtime for {recipient_phone}! "
                    elif amount:
                        context_prefix = f"Continuing your ₦{amount:,.0f} airtime purchase! "
                    else:
                        context_prefix = "Continuing where you left off! "
                    
                    logger.info(f"Airtime flow resume detected, replaying saved response with context")
                    return f"{context_prefix}{saved_response}"
            
            # Fallback to last_response
            last_response_key = f"user:{phone_number}:last_response"
            last_response = await self.redis_client.get(last_response_key)
            if last_response:
                logger.info(f"Airtime flow resume detected, replaying last response")
                return f"Continuing your airtime purchase! {last_response}"

        # Try to load existing checkpoint state first
        input_state = None
        checkpoint_is_stale = False
        try:
            current_state = await self.graph.aget_state(config)
            if current_state and current_state.values:
                loaded_state = dict(current_state.values)
                # Check if checkpoint is stale (cancelled, completed, error, or waiting for auth)
                # If user starts a new purchase request, old pending states should be cleared
                flow_state = loaded_state.get("flow_state")
                airtime_status = loaded_state.get("airtime_status")
                if flow_state in ("cancelled", "completed", "error", "authorizing", "confirming") or airtime_status in ("completed", "failed", "cancelled"):
                    checkpoint_is_stale = True
                else:
                    input_state = loaded_state
        except Exception:
            pass
        
        # Clear stale checkpoint
        if checkpoint_is_stale:
            await self.clear_checkpoint(phone_number)
            input_state = None
        
        # If no checkpoint or checkpoint was stale, create initial state
        if input_state is None:
            input_state = create_initial_state(
                phone_number, message, message_id, classification_result)
        else:
            # Merge new message into existing state
            input_state["message"] = message
            input_state["message_id"] = message_id
            input_state["phone_number"] = phone_number
            input_state["response"] = ""  # Clear previous response
            input_state["llm_reply"] = None  # Clear previous llm_reply
            if classification_result:
                input_state["classification_result"] = classification_result
        
        if classification_result and "detected_language" in classification_result:
             input_state["language"] = classification_result["detected_language"]
        
        final_state = await self.graph.ainvoke(cast(AirtimeState, input_state), config)
        await update_conversation_state(phone_number, cast(AirtimeState, final_state))
        
        # Debug: log final state
        from shared.utils.logging import get_logger
        logger = get_logger(__name__)
        logger.info(
            "airtime_flow_completed",
            flow_state=final_state.get("flow_state"),
            airtime_status=final_state.get("airtime_status"),
            response_length=len(final_state.get("response", "") or ""),
            has_response=bool(final_state.get("response")),
        )

        if self.completion_callback:
            airtime_status = final_state.get("airtime_status")
            if airtime_status in ("completed", "failed", "cancelled"):
                completion_result = {
                    "status": airtime_status,
                    "amount": final_state.get("amount"),
                    "recipient_phone": final_state.get("recipient_phone"),
                    "response": final_state.get("response", ""),
                }
                asyncio.create_task(
                    self.completion_callback.on_flow_complete(
                        phone_number, "airtime", completion_result
                    )
                )

        return final_state.get("response", "")

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, pin_error: Optional[str] = None
    ) -> str:
        """
        Resume graph execution after PIN verification using LangGraph interrupt pattern.
        
        The graph is compiled with interrupt_before=["authorize"], so after confirm
        sends the PIN flow, the graph pauses at the authorize node. This method:
        1. Updates the state with PIN verification result
        2. Resumes the graph with ainvoke(None, config)
        3. The authorize node then runs and processes the transaction

        Args:
            phone_number: User's phone number
            pin_verified: Whether PIN was verified successfully
            pin_error: Error message if PIN verification failed

        Returns:
            Response message
        """
        from shared.utils.logging import get_logger
        
        logger = get_logger(__name__)
        
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"airtime:{phone_number}",
            }
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        current_state = await self.graph.aget_state(config)
        if not current_state or not current_state.values:
            return "No active airtime purchase session found."

        # Update state with PIN verification result using aupdate_state
        await self.graph.aupdate_state(
            config,
            {
                "pin_verified": pin_verified,
                "pin_verification_error": pin_error,
            }
        )

        logger.info(
            "airtime_pin_resume_starting",
            phone=phone_number,
            pin_verified=pin_verified,
        )

        # Resume graph execution from the interrupt point (authorize node)
        # Passing None resumes from where the graph was interrupted
        final_state = await self.graph.ainvoke(None, config)
        
        await update_conversation_state(phone_number, cast(AirtimeState, final_state))
        
        logger.info(
            "airtime_pin_resume_completed",
            flow_state=final_state.get("flow_state"),
            airtime_status=final_state.get("airtime_status"),
            response_length=len(final_state.get("response", "") or ""),
            has_response=bool(final_state.get("response")),
            pin_verified=final_state.get("pin_verified"),
        )

        if self.completion_callback:
            airtime_status = final_state.get("airtime_status")
            if airtime_status in ("completed", "failed", "cancelled"):
                completion_result = {
                    "status": airtime_status,
                    "amount": final_state.get("amount"),
                    "recipient_phone": final_state.get("recipient_phone"),
                    "response": final_state.get("response", ""),
                }
                asyncio.create_task(
                    self.completion_callback.on_flow_complete(
                        phone_number, "airtime", completion_result
                    )
                )

        return final_state.get("response", "")
