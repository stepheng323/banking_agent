"""LangGraph graph for transfer flow."""

import json
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
from apps.core.src.agent.services.user_data_cache import UserDataCache
from shared.queue.redis_queue import RedisQueue
from shared.config.settings import settings
from apps.core.src.agent.services.validation_service import AsyncValidationService
from apps.core.src.agent.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.transfer.state import TransferState

from .builder import build_graph
from .state import (
    create_initial_state,
    update_conversation_state,
    get_transfer_session_age,
    start_transfer_session,
    clear_transfer_session,
    has_substantial_transfer_data,
    clear_all_transfer_state,
)
from shared.utils.logging import get_logger

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
            provider = PaymentProviderFactory.get_provider_for_service(
                "resolve_account")
        except Exception:
            provider = None
        self.validation_service = AsyncValidationService(
            provider) if provider else None

        self.redis_client = RedisClient.get_client()
        self.bank_cache = BankCacheService(redis_client=self.redis_client)
        self.payment_provider = provider  # Store for bank fetching
        self.queue = queue

        self.graph = None
        self._checkpointer = None
        self._checkpointer_setup = False

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear transfer flow checkpoint for a user."""
        try:
            await self._ensure_checkpointer()
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": f"transfer:{phone_number}",
                }
            }
            # Use the compiled graph's adelete method (graph is compiled with checkpointer)
            # Use the checkpointer's adelete_thread method directly
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
            # Use Redis Stack checkpointer (<1ms latency, includes RediSearch module)
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

    async def run(self, phone_number: str, message: str, message_id: str, classification_result: Optional[dict] = None) -> str:
        """Run the transfer flow graph."""
        logger.info(f"run called with message: '{message}'")
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"transfer:{phone_number}",
            }
        }

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        # Check if user is responding to cancellation prompt
        message_lower = message.lower().strip()
        last_response_key = f"user:{phone_number}:last_response"
        last_response = await self.redis_client.get(last_response_key)
        last_response_lower = (last_response or "").lower()

        is_cancellation_confirmation = (
            message_lower in ("yes", "y", "cancel", "ok", "proceed", "sure", "yeah", "yep") and
            "cancel it and start a new transfer" in last_response_lower
        )
        is_cancellation_decline = (
            message_lower in ("no", "n", "continue", "ni", "nah", "nope", "don't", "dont") and
            "cancel it and start a new transfer" in last_response_lower
        )

        # If user confirmed cancellation, clear all state before processing
        if is_cancellation_confirmation:
            await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
            # Start fresh with new message
            input_state = create_initial_state(
                phone_number, message, message_id, classification_result)
            final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
            await update_conversation_state(phone_number, cast(TransferState, final_state))
            return final_state.get("response", "")

        # If user declined cancellation, continue with previous transfer
        if is_cancellation_decline:
            # Get transfer details from conversation_state
            try:
                redis_client = RedisClient.get_client()
                conv_state_key = f"user:{phone_number}:conversation_state"
                conv_state_data = await redis_client.get(conv_state_key)

                if conv_state_data:
                    conv_state = json.loads(conv_state_data)
                    prev_amount = conv_state.get("amount", 0)
                    prev_recipient_name = conv_state.get("recipient_name")
                    prev_recipient_account = conv_state.get(
                        "recipient_account", "")
                    prev_recipient = prev_recipient_name or prev_recipient_account or "the recipient"
                    transfer_status = conv_state.get("transfer_status")

                    # Return acknowledgment message
                    if transfer_status == "pending":
                        return f"Got it. Continuing with your pending transfer of ₦{prev_amount:,.2f} to {prev_recipient}. Please enter your PIN to confirm."
                    else:
                        return f"Got it. Continuing with your transfer of ₦{prev_amount:,.2f} to {prev_recipient}."
            except Exception:
                pass

            # Fallback: Try checkpoint
            try:
                current_state = await self.graph.aget_state(config)
                if current_state and current_state.values:
                    prev_amount = current_state.values.get("amount", 0)
                    prev_recipient = current_state.values.get(
                        "recipient_name") or current_state.values.get("recipient_account", "")
                    transfer_status = current_state.values.get(
                        "transfer_status")

                    if transfer_status == "pending":
                        return f"Got it. Continuing with your pending transfer of ₦{prev_amount:,.2f} to {prev_recipient}. Please enter your PIN to confirm."
                    else:
                        return f"Got it. Continuing with your transfer of ₦{prev_amount:,.2f} to {prev_recipient}."
            except Exception:
                pass
            return "Got it. Continuing with your previous transfer."

        # Load existing checkpoint state to preserve values from previous turns
        try:
            current_state = await self.graph.aget_state(config)
            if current_state and current_state.values:
                input_state = dict(current_state.values)
                
                # CRITICAL: If checkpoint has collection_complete status and we're starting a new task,
                # clear the transfer_status to allow the new task to process normally
                checkpoint_transfer_status = input_state.get("transfer_status")
                has_task_params = classification_result and "task_parameters" in classification_result
                # Check if message contains account numbers (indicates continuation, not new task)
                import re
                has_account_number = bool(re.search(r'\b\d{10}\b', message))
                is_new_task_start = has_task_params and not has_account_number
                
                if checkpoint_transfer_status == "collection_complete" and is_new_task_start:
                    logger.debug(f"Clearing stale collection_complete status for new task")
                    input_state["transfer_status"] = None
                    input_state["flow_state"] = "extracting"
                    # CRITICAL: Clear ALL task-specific data for new task to prevent data leakage
                    input_state["recipient_account"] = None
                    input_state["recipient_bank_code"] = None
                    input_state["recipient_bank_name"] = None
                    input_state["account_resolved"] = None
                    input_state["amount"] = None  # Clear amount to force restoration from task_parameters
                    input_state["recipient_name"] = None
                    input_state["idempotency_key"] = None  # Clear to force new key generation
                
                # CRITICAL FIX: Always update message FIRST, before any other logic
                # This ensures the user's actual message is used for extraction, not the old checkpoint message
                old_message = input_state.get("message", "")
                input_state["message"] = message
                input_state["phone_number"] = phone_number
                input_state["message_id"] = message_id
                if classification_result:
                    input_state["classification_result"] = classification_result
                    
                    # CRITICAL: Always restore task parameters when they exist
                    # This ensures each task uses its own amount and recipient, not values from previous task
                    if "task_parameters" in classification_result:
                        task_params = classification_result.get("task_parameters", {})
                        task_amount = task_params.get("amount")
                        task_recipient = task_params.get("recipient")
                        
                        if task_amount:
                            input_state["amount"] = float(task_amount)
                            logger.debug(f"Set amount from task_parameters: {task_amount}")
                        
                        if task_recipient:
                            input_state["recipient_name"] = task_recipient
                            logger.debug(f"Set recipient_name from task_parameters: {task_recipient}")
                
                logger.debug(f"Message update - old: '{old_message}' -> new: '{message}'")
                
                checkpoint_transfer_status = input_state.get("transfer_status")
                
                # Check if transaction is complete or session expired
                session_age = await get_transfer_session_age(phone_number)
                is_terminal_status = checkpoint_transfer_status in ("authorized", "completed", "failed")
                is_session_expired = session_age is not None and session_age >= settings.flow_session_timeout
                
                # Clear state if transaction complete or session expired
                if is_terminal_status or is_session_expired:
                    await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
                    input_state = create_initial_state(
                        phone_number, message, message_id, classification_result)
                else:
                    # Check if this is a new transfer intent
                    is_new_transfer_intent = False
                    is_non_transfer_intent = False
                    if classification_result:
                        classification_intent = classification_result.get("intent", "").lower()
                        is_new_transfer_intent = classification_intent in {
                            "transfer", "send_money", "send money", "send", "pay"
                        }
                        # Check if this is clearly NOT a transfer intent (conversational, query, etc.)
                        is_non_transfer_intent = classification_intent in {
                            "conversational", "query", "utility", "cancel"
                        }
                    
                    # If this is NOT a transfer intent, clear checkpoint and return early
                    # The transfer flow shouldn't be called for non-transfer intents
                    # This is a safeguard in case classification is wrong
                    if is_non_transfer_intent:
                        await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
                        # Return empty string to let orchestrator handle routing
                        # The orchestrator should route to conversation responder
                        return ""
                    
                    # If new intent detected and we have old values, clear them
                    # BUT: Don't clear if this is a complex transfer (has task_parameters) - we need to preserve amount
                    has_task_params = classification_result and "task_parameters" in classification_result
                    is_continuing_flow = input_state.get("flow_state") in ("collecting_recipient", "collecting_amount", "validating", "confirming")
                    
                    if is_new_transfer_intent and (input_state.get("amount") or input_state.get("recipient_account")):
                        # Only clear if this is truly a NEW transfer (not continuing an existing one or complex transfer)
                        if not has_task_params and not is_continuing_flow:
                            logger.debug(f"Clearing old transfer values - new intent detected, not a complex transfer")
                            # Clear old transfer values
                            input_state["amount"] = None
                            input_state["recipient_account"] = None
                            input_state["recipient_bank_code"] = None
                            input_state["recipient_bank_name"] = None
                            input_state["recipient_name"] = None
                            input_state["idempotency_key"] = None
                            input_state["transfer_status"] = None
                            input_state["account_resolved"] = None
                            input_state["matched_beneficiary"] = None
                            input_state["flow_state"] = "extracting"
                            # Clear Redis previous values key
                            prev_key = f"transfer:prev:{phone_number}:{input_state.get('idempotency_key', '')}"
                            await self.redis_client.delete(prev_key)
                        else:
                            logger.debug(f"Preserving transfer values - has_task_params={has_task_params}, is_continuing_flow={is_continuing_flow}")

                # Session-based transfer management
                stale_transfer_status = input_state.get("transfer_status")
                has_substantial_data = has_substantial_transfer_data(
                    cast(TransferState, input_state))

                has_amount_keywords = any(keyword in message_lower for keyword in [
                    "send", "transfer", "pay", "give"
                ]) or any(char in message for char in ["k", "₦"]) or any(word in message_lower for word in ["thousand", "naira"])

                if has_amount_keywords and has_substantial_data:
                    if session_age is not None:
                        if session_age < settings.flow_session_timeout:
                            amount = input_state.get("amount", 0)
                            recipient_name = input_state.get("recipient_name")
                            recipient_account = input_state.get("recipient_account")

                            if recipient_name:
                                recipient_display = recipient_name
                            elif recipient_account:
                                recipient_display = f"account {recipient_account[-4:]}"
                            else:
                                recipient_display = "the recipient"

                            cancellation_prompt = f"You have a pending transfer of ₦{amount:,.2f} to {recipient_display}. Would you like to cancel it and start a new transfer? (Reply 'yes' to cancel, 'no' to continue with the previous transfer)"

                            input_state["response"] = cancellation_prompt
                            input_state["flow_state"] = "extracting"

                            prompt_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
                            await update_conversation_state(phone_number, cast(TransferState, prompt_state))

                            return cancellation_prompt
                        else:
                            await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
                            input_state = create_initial_state(
                                phone_number, message, message_id, classification_result)
                    else:
                        await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
                        input_state = create_initial_state(
                            phone_number, message, message_id, classification_result)

                # Context-aware clearing: only clear recipient data when it's a new transfer
                stale_recipient = input_state.get("recipient_account")
                stale_bank = input_state.get(
                    "recipient_bank_code") or input_state.get("recipient_bank_name")
                stale_transfer_status = input_state.get("transfer_status")
                current_flow_state = input_state.get("flow_state")

                message_lower = message.lower()
                has_amount_keywords = any(keyword in message_lower for keyword in [
                    "send", "transfer", "pay", "give"
                ]) or any(char in message for char in ["k", "₦"]) or any(word in message_lower for word in ["thousand", "naira"])

                # Improved regex to catch account numbers with commas/spaces: "8162511023, Access bank"
                account_numbers_in_message = re.findall(r'\b\d{10}\b', message.replace(",", " ").replace(".", " "))
                has_account_in_message = len(account_numbers_in_message) > 0
                
                # Debug logging
                current_amount = input_state.get("amount")
                logger.debug(f"Message: '{message}', has_amount_keywords={has_amount_keywords}, has_account_in_message={has_account_in_message}, account_numbers={account_numbers_in_message}")
                logger.debug(f"State: amount={current_amount}, stale_recipient={stale_recipient}, stale_bank={stale_bank}, flow_state={current_flow_state}, transfer_status={stale_transfer_status}")

                should_clear_recipient = False
                # CRITICAL FIX: Don't clear recipient if user is providing account details (collecting_recipient state)
                # Also don't clear if account number is in the message
                # Don't clear if transfer_status is collection_complete (complex transfer, data is needed for summary)
                if (has_amount_keywords and
                    (stale_recipient or stale_bank) and
                    current_flow_state not in ("collecting_recipient", "collecting_amount", "confirming") and
                    stale_transfer_status not in ("pending", "collection_complete")):
                    if not has_account_in_message:
                        should_clear_recipient = True
                        logger.debug(f"Will clear recipient: has_amount_keywords={has_amount_keywords}, has_account_in_message={has_account_in_message}, flow_state={current_flow_state}, transfer_status={stale_transfer_status}")

                if should_clear_recipient:
                    logger.debug(f"Clearing stale recipient data")
                    input_state["recipient_account"] = None
                    input_state["recipient_bank_code"] = None
                    input_state["recipient_bank_name"] = None
                    input_state["recipient_name"] = None
                    input_state["account_resolved"] = None
                    input_state["matched_beneficiary"] = None
                    input_state["validation_errors"] = []
                    input_state["narration"] = None

                if stale_transfer_status in ("completed", "failed", "cancelled"):
                    input_state["recipient_account"] = None
                    input_state["recipient_bank_code"] = None
                    input_state["recipient_bank_name"] = None
                    input_state["recipient_name"] = None
                    input_state["account_resolved"] = None
                    input_state["matched_beneficiary"] = None
                    input_state["validation_errors"] = []
                    input_state["transfer_status"] = None
                    input_state["idempotency_key"] = None
                    input_state["flow_state"] = "extracting"
                    input_state["amount"] = None
                    input_state["narration"] = None
            else:
                input_state = create_initial_state(
                    phone_number, message, message_id, classification_result)
        except Exception:
            input_state = create_initial_state(
                phone_number, message, message_id, classification_result)

        final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
        await update_conversation_state(phone_number, cast(TransferState, final_state))

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
                        "account_resolved": final_state.get("account_resolved"),  # Include resolved account name
                        "selected_source_account": final_state.get("selected_source_account"),
                        "response": final_state.get("response", ""),
                    }
                    asyncio.create_task(
                        self.completion_callback.on_flow_complete(
                            phone_number, "transfer", result
                        )
                    )

        return final_state.get("response", "")

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, pin_error: Optional[str] = None
    ) -> str:
        """
        Resume graph execution after PIN verification.

        Args:
            phone_number: User's phone number
            pin_verified: Whether PIN was verified successfully
            pin_error: Error message if PIN verification failed

        Returns:
            Response message
        """
        await self._ensure_checkpointer()

        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"transfer:{phone_number}",
            }
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
            "transfer_status": "pending",  # Clear collection_complete status to allow authorization
            "skip_confirmation_display": True,  # Skip showing confirmation again
        })

        final_state = await self.graph.ainvoke(cast(TransferState, updated_state), config)
        await update_conversation_state(phone_number, cast(TransferState, final_state))

        # Get response from final state
        response = final_state.get("response", "")
        transfer_status = final_state.get("transfer_status")

        # Fallback if no response but transaction was authorized/completed
        if not response and pin_verified and transfer_status in ("authorized", "completed"):
            response = "Transfer authorized. Processing your request..."

        if self.completion_callback:
            transfer_status = final_state.get("transfer_status")
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
