"""LangGraph graph for transfer flow."""

import os
import re
import json

from typing import Optional, cast
import asyncio

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from shared.cache.redis_client import RedisClient
from shared.clients.payment_provider_factory import PaymentProviderFactory
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.cache.bank_cache import BankCacheService
from shared.cache.user_context_cache import UserContextCacheService
from shared.queue.redis_queue import RedisQueue
from shared.config import settings
from apps.core.src.agent.services.validation_service import AsyncValidationService
from apps.core.src.agent.services.beneficiary_matcher import BeneficiaryMatcher
from apps.core.src.agent.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.services.flow_completion_callback import FlowCompletionCallback
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


class TransferFlowGraph:
    """LangGraph-based transfer flow."""

    def __init__(
        self,
        user_cache: UserContextCacheService,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        extractor: TransferEntityExtractor,
        queue: RedisQueue,
        completion_callback: Optional[FlowCompletionCallback] = None,
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
        self._checkpointer_cm = None
        self._checkpointer = None
        self._checkpointer_setup = False

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            db_url = os.getenv("DATABASE_URL", "")
            if not db_url:
                raise ValueError("DATABASE_URL required for checkpointing")
            self._checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_url)
            # type: ignore[method-assign,attr-defined]
            self._checkpointer = await self._checkpointer_cm.__aenter__()
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
                    if classification_result:
                        classification_intent = classification_result.get("intent", "").lower()
                        is_new_transfer_intent = classification_intent in {
                            "transfer", "send_money", "send money", "send", "pay"
                        }
                    
                    # If new intent detected and we have old values, clear them
                    if is_new_transfer_intent and (input_state.get("amount") or input_state.get("recipient_account")):
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
                    
                    input_state.update({
                        "phone_number": phone_number,
                        "message": message,
                        "message_id": message_id,
                        "classification_result": classification_result,
                    })

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

                account_numbers_in_message = re.findall(r'\b\d{10}\b', message)
                has_account_in_message = len(account_numbers_in_message) > 0

                should_clear_recipient = False
                if (has_amount_keywords and
                    (stale_recipient or stale_bank) and
                    current_flow_state not in ("collecting_recipient", "collecting_amount") and
                        stale_transfer_status != "pending"):
                    if not has_account_in_message:
                        should_clear_recipient = True

                if should_clear_recipient:
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
                if transfer_status in ("completed", "failed", "cancelled"):
                    result = {
                        "status": transfer_status,
                        "amount": final_state.get("amount"),
                        "recipient_account": final_state.get("recipient_account"),
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
