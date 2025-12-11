"""LangGraph graph for transfer flow."""

from typing import Optional, cast, TYPE_CHECKING
import asyncio

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from shared.cache.redis_client import RedisClient
from shared.clients.payment_provider_factory import PaymentProviderFactory
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.cache.bank_cache import BankCacheService
from apps.core.src.agent.tools.cache.user_data import UserDataCache
from shared.queue.redis_queue import RedisQueue
from shared.config.settings import settings
from apps.core.src.agent.tools.validation.service import AsyncValidationService
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.sub_agents.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.sub_agents.transfer.state import TransferState

from .builder import build_graph
from .run_context import TransferRunContext
from .cancellation import (
    is_cancellation_confirmation,
    is_cancellation_decline,
    handle_cancellation_confirmation,
    handle_cancellation_decline_with_checkpoint,
    should_prompt_for_cancellation,
    build_cancellation_prompt,
)
from .checkpoint_manager import (
    load_checkpoint_state,
    prepare_checkpoint_state,
    clear_all_transfer_state,
)
from .state import (
    create_initial_state,
    update_conversation_state,
    start_transfer_session,
    clear_transfer_session,
    has_substantial_transfer_data,
    get_transfer_session_age,
)
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.orchestrator.flow_completion_callback import FlowCompletionCallback

logger = get_logger(__name__)


class TransferFlowGraph:
    """LangGraph-based transfer flow."""

    def __init__(
        self,
        user_cache: UserDataCache,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        extractor: TransferEntityExtractor,
        queue: RedisQueue,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ):
        self.user_cache = user_cache
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.completion_callback = completion_callback

        try:
            provider = PaymentProviderFactory.get_provider_for_service("resolve_account")
        except Exception:
            provider = None
        self.validation_service = AsyncValidationService(provider) if provider else None

        self.redis_client = RedisClient.get_client()
        self.bank_cache = BankCacheService(redis_client=self.redis_client)
        self.payment_provider = provider
        self.queue = queue

        self.graph = None
        self._checkpointer = None
        self._checkpointer_setup = False

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear transfer flow checkpoint for a user."""
        try:
            await self._ensure_checkpointer()
            config: RunnableConfig = {
                "configurable": {"thread_id": f"transfer:{phone_number}"}
            }
            if self._checkpointer:
                thread_id = config["configurable"]["thread_id"]
                await self._checkpointer.adelete_thread(thread_id)
                logger.info(f"Cleared transfer checkpoint for {phone_number}")
            else:
                logger.warning(f"Checkpointer not initialized, cannot clear checkpoint for {phone_number}")
        except Exception as e:
            logger.error(f"Error clearing transfer checkpoint: {e}")

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
                validation_service=self.validation_service,
                bank_cache=self.bank_cache,
                payment_provider=self.payment_provider,
                whatsapp_client=self.whatsapp_client,
                redis_client=self.redis_client,
                queue=self.queue,
            ).compile(checkpointer=self._checkpointer)

    async def run(
        self,
        phone_number: str,
        message: str,
        message_id: str,
        classification_result: Optional[dict] = None,
        image_data: str | None = None
    ) -> str:
        """Run the transfer flow graph."""
        logger.info(f"run called with message: '{message}'")
        await self._ensure_checkpointer()

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        # Create run context
        config: RunnableConfig = {
            "configurable": {"thread_id": f"transfer:{phone_number}"}
        }
        ctx = TransferRunContext(
            phone_number=phone_number,
            message=message,
            message_id=message_id,
            classification_result=classification_result,
            image_data=image_data,
            config=config,
        )

        # Check for cancellation confirmation/decline
        last_response_key = f"user:{phone_number}:last_response"
        last_response = await self.redis_client.get(last_response_key)

        if is_cancellation_confirmation(ctx, last_response or ""):
            return await handle_cancellation_confirmation(ctx, self.graph, self.redis_client)

        if is_cancellation_decline(ctx, last_response or ""):
            return await handle_cancellation_decline_with_checkpoint(ctx, self.graph)

        # Load and prepare state
        input_state = await load_checkpoint_state(ctx, self.graph)
        
        if input_state:
            input_state = await prepare_checkpoint_state(
                ctx, input_state, self.graph, self.redis_client
            )
            if input_state is None:
                # Non-transfer intent, return empty to let orchestrator handle
                return ""
            
            # Check if should prompt for cancellation
            if await should_prompt_for_cancellation(ctx, input_state):
                return await self._handle_cancellation_prompt(ctx, input_state)
        else:
            input_state = create_initial_state(
                phone_number, message, message_id, classification_result, image_data=image_data
            )

        # Invoke graph
        final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
        await update_conversation_state(phone_number, cast(TransferState, final_state))

        # Post-processing
        await self._handle_post_processing(phone_number, final_state)

        return final_state.get("response", "")

    async def _handle_cancellation_prompt(
        self,
        ctx: TransferRunContext,
        input_state: dict,
    ) -> str:
        """Handle prompting user for cancellation."""
        cancellation_prompt = build_cancellation_prompt(input_state)
        
        input_state["response"] = cancellation_prompt
        input_state["flow_state"] = "extracting"

        prompt_state = await self.graph.ainvoke(
            cast(TransferState, input_state), ctx.config
        )
        await update_conversation_state(ctx.phone_number, cast(TransferState, prompt_state))

        return cancellation_prompt

    async def _handle_post_processing(
        self,
        phone_number: str,
        final_state: dict,
    ) -> None:
        """Handle post-processing after graph execution."""
        if has_substantial_transfer_data(cast(TransferState, final_state)):
            await start_transfer_session(phone_number)
        elif final_state.get("transfer_status") not in ("pending", None):
            await clear_transfer_session(phone_number)

            if self.completion_callback:
                transfer_status = final_state.get("transfer_status")
                if transfer_status in ("completed", "failed", "cancelled", "collection_complete"):
                    result = {
                        "status": transfer_status,
                        "amount": final_state.get("amount"),
                        "recipient_account": final_state.get("recipient_account"),
                        "recipient_bank_name": final_state.get("recipient_bank_name"),
                        "recipient_bank_code": final_state.get("recipient_bank_code"),
                        "account_resolved": final_state.get("account_resolved"),
                        "selected_source_account": final_state.get("selected_source_account"),
                        "response": final_state.get("response", ""),
                    }
                    asyncio.create_task(
                        self.completion_callback.on_flow_complete(
                            phone_number, "transfer", result
                        )
                    )

    async def resume_after_pin_verification(
        self,
        phone_number: str,
        pin_verified: bool,
        pin_error: Optional[str] = None
    ) -> str:
        """Resume graph execution after PIN verification."""
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {"thread_id": f"transfer:{phone_number}"}
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        current_state = await self.graph.aget_state(config)
        if not current_state or not current_state.values:
            return "No active transfer session found."

        updated_state = dict(current_state.values)
        updated_state.update({
            "phone_number": phone_number,
            "message": "",
            "message_id": "",
            "pin_verified": pin_verified,
            "pin_verification_error": pin_error,
            "flow_state": "authorizing",
            "transfer_status": "pending",
            "skip_confirmation_display": True,
            "llm_reply": None,
            "response": "",
        })

        logger.info(
            "resume_after_pin_verification_invoking",
            phone=phone_number,
            flow_state=updated_state.get("flow_state"),
            transfer_status=updated_state.get("transfer_status"),
            pin_verified=pin_verified,
        )

        final_state = await self.graph.ainvoke(cast(TransferState, updated_state), config)

        logger.info(
            "resume_after_pin_verification_completed",
            phone=phone_number,
            flow_state=final_state.get("flow_state"),
            transfer_status=final_state.get("transfer_status"),
            response_preview=final_state.get("response", "")[:100] if final_state.get("response") else None,
        )

        await update_conversation_state(phone_number, cast(TransferState, final_state))

        response = final_state.get("response", "")
        transfer_status = final_state.get("transfer_status")

        if not response and pin_verified and transfer_status in ("authorized", "completed"):
            response = "Transfer authorized. Processing your request..."

        if self.completion_callback:
            if transfer_status in ("completed", "failed", "cancelled"):
                completion_result = {
                    "status": transfer_status,
                    "amount": final_state.get("amount"),
                    "recipient_account": final_state.get("recipient_account"),
                    "response": response,
                }
                asyncio.create_task(
                    self.completion_callback.on_flow_complete(
                        phone_number, "transfer", completion_result
                    )
                )

        return response
