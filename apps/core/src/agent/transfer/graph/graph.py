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
    TRANSFER_SESSION_TIMEOUT,
)
from .utils import debug_log


class TransferFlowGraph:
    """LangGraph-based transfer flow."""

    def __init__(
        self,
        user_cache: UserContextCacheService,
        beneficiary_repo: BeneficiaryRepository,
        account_repo: AccountRepository,
        whatsapp_client: WhatsAppClient,
        extractor: TransferEntityExtractor,
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
            debug_log(
                "✅ User confirmed cancellation - clearing all state before processing new transfer")
            await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
            # Start fresh with new message
            input_state = create_initial_state(
                phone_number, message, message_id, classification_result)
            final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
            await update_conversation_state(phone_number, cast(TransferState, final_state))
            return final_state.get("response", "")

        # If user declined cancellation, continue with previous transfer
        if is_cancellation_decline:
            debug_log(
                "ℹ️  User declined cancellation - continuing with previous transfer")
            # Get transfer details from conversation_state (more reliable than checkpoint which may have been updated)
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
            except Exception as e:
                debug_log(
                    f"⚠️  Error loading conversation_state for 'No' response: {e}")

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
            # If both fail, return generic message
            return "Got it. Continuing with your previous transfer."

        # Load existing checkpoint state to preserve values from previous turns
        # LangGraph's ainvoke REPLACES state, so we must load checkpoint first
        # Use the graph's get_state method to get current checkpoint
        try:
            current_state = await self.graph.aget_state(config)
            if current_state and current_state.values:
                # Merge checkpoint state with new message fields
                input_state = dict(current_state.values)
                input_state.update({
                    "phone_number": phone_number,
                    "message": message,
                    "message_id": message_id,
                    # Update classification result from orchestrator
                    "classification_result": classification_result,
                })

                # SESSION-BASED TRANSFER MANAGEMENT
                # Check if there's a pending transfer with substantial data
                stale_transfer_status = input_state.get("transfer_status")
                has_substantial_data = has_substantial_transfer_data(
                    cast(TransferState, input_state))
                session_age = await get_transfer_session_age(phone_number)

                # Check if message indicates a NEW transfer attempt
                has_amount_keywords = any(keyword in message_lower for keyword in [
                    "send", "transfer", "pay", "give"
                ]) or any(char in message for char in ["k", "₦"]) or any(word in message_lower for word in ["thousand", "naira"])

                if has_amount_keywords and has_substantial_data:
                    if session_age is not None:
                        if session_age < TRANSFER_SESSION_TIMEOUT:
                            # Within session: Prompt user to cancel previous transfer
                            amount = input_state.get("amount", 0)
                            recipient_name = input_state.get("recipient_name")
                            recipient_account = input_state.get(
                                "recipient_account")

                            # Format recipient display
                            if recipient_name:
                                recipient_display = recipient_name
                            elif recipient_account:
                                recipient_display = f"account {recipient_account[-4:]}"
                            else:
                                recipient_display = "the recipient"

                            debug_log(
                                f"⏸️  Session active ({session_age:.1f}s < {TRANSFER_SESSION_TIMEOUT}s) - prompting for cancellation")

                            cancellation_prompt = f"You have a pending transfer of ₦{amount:,.2f} to {recipient_display}. Would you like to cancel it and start a new transfer? (Reply 'yes' to cancel, 'no' to continue with the previous transfer)"

                            # Update state with cancellation prompt and run through graph to save state
                            input_state["response"] = cancellation_prompt
                            input_state["flow_state"] = "extracting"

                            # Run through graph to save state properly
                            prompt_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
                            await update_conversation_state(phone_number, cast(TransferState, prompt_state))

                            return cancellation_prompt
                        else:
                            # Session expired: Auto-clear all state
                            debug_log(
                                f"⏰ Transfer session expired ({session_age:.1f}s > {TRANSFER_SESSION_TIMEOUT}s) - clearing all state")
                            await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
                            input_state = create_initial_state(
                                phone_number, message, message_id, classification_result)
                    else:
                        # No session but has substantial data (edge case) - clear it
                        debug_log(
                            "🧹 No active session but found substantial data - clearing state")
                        await clear_all_transfer_state(phone_number, self.redis_client, self.graph, config)
                        input_state = create_initial_state(
                            phone_number, message, message_id, classification_result)

                # CONTEXT-AWARE CLEARING: Only clear recipient data when it's actually a NEW transfer
                # Don't clear when we're in the middle of collecting recipient information
                stale_recipient = input_state.get("recipient_account")
                stale_bank = input_state.get(
                    "recipient_bank_code") or input_state.get("recipient_bank_name")
                stale_transfer_status = input_state.get("transfer_status")
                current_flow_state = input_state.get("flow_state")

                # Check if message contains a NEW amount (indicating a new transfer)
                # Look for amount keywords that would indicate user is starting a new transfer
                message_lower = message.lower()
                has_amount_keywords = any(keyword in message_lower for keyword in [
                    "send", "transfer", "pay", "give"
                ]) or any(char in message for char in ["k", "₦"]) or any(word in message_lower for word in ["thousand", "naira"])

                # Extract potential amount from message (simple check for numbers with k/thousand/naira)
                account_numbers_in_message = re.findall(r'\b\d{10}\b', message)
                has_account_in_message = len(account_numbers_in_message) > 0

                # Only clear recipient data if ALL of these are true:
                # 1. Message has amount keywords (potential new transfer)
                # 2. We're NOT in "collecting_recipient" state (not continuing a multi-turn conversation)
                # 3. Transfer is NOT "pending" (not an active transfer in progress)
                # 4. We have stale recipient data
                should_clear_recipient = False
                if (has_amount_keywords and
                    (stale_recipient or stale_bank) and
                    current_flow_state not in ("collecting_recipient", "collecting_amount") and
                        stale_transfer_status != "pending"):
                    # Check if this looks like a new transfer (has amount but no recipient account)
                    if not has_account_in_message:
                        # No account number in message + has amount keywords + not collecting recipient + not pending
                        # This likely indicates a new transfer request
                        should_clear_recipient = True
                        debug_log(
                            f"🧹 GRAPH INIT: Clearing stale recipient data - new transfer detected. Stale: account={stale_recipient}, bank={stale_bank}, status={stale_transfer_status}, flow_state={current_flow_state}")

                if should_clear_recipient:
                    input_state["recipient_account"] = None
                    input_state["recipient_bank_code"] = None
                    input_state["recipient_bank_name"] = None
                    input_state["recipient_name"] = None
                    input_state["account_resolved"] = None
                    input_state["matched_beneficiary"] = None
                    input_state["validation_errors"] = []
                    # Clear narration for new transfer
                    input_state["narration"] = None
                else:
                    # Preserve recipient data - we're either continuing recipient collection or transfer is pending
                    if stale_recipient or stale_bank:
                        debug_log(
                            f"ℹ️ GRAPH INIT: Preserving recipient data - continuing collection or active transfer. account={stale_recipient}, bank={stale_bank}, status={stale_transfer_status}, flow_state={current_flow_state}")

                # Clear all state if transfer was completed/cancelled/failed (definite end of transfer)
                if stale_transfer_status in ("completed", "failed", "cancelled"):
                    debug_log(
                        f"🧹 GRAPH INIT: Clearing state from {stale_transfer_status} transfer")
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

        # Update conversation_state in Redis so orchestrator can detect active transactions
        await update_conversation_state(phone_number, cast(TransferState, final_state))

        # Start or update transfer session if we have substantial data
        if has_substantial_transfer_data(cast(TransferState, final_state)):
            await start_transfer_session(phone_number)
        elif final_state.get("transfer_status") not in ("pending", None):
            # Transfer completed/cancelled - clear session
            await clear_transfer_session(phone_number)

            # Call completion callback if flow completed or failed
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
