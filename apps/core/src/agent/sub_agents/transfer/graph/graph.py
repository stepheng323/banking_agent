"""LangGraph graph for transfer flow."""

import asyncio
from typing import TYPE_CHECKING, Optional, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from apps.core.src.agent.sub_agents.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.tools.validation.service import AsyncValidationService
from shared.cache.bank_cache import BankCacheService
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.factories.payment import PaymentProviderFactory
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.utils.logging import get_logger

from .builder import build_graph
from .cancellation import (
    handle_cancellation_confirmation,
    handle_cancellation_decline_with_checkpoint,
    is_cancellation_confirmation,
    is_cancellation_decline,
)
from .checkpoint_manager import (
    load_checkpoint_state,
    prepare_checkpoint_state,
)
from .run_context import TransferRunContext
from .state import (
    clear_transfer_session,
    create_initial_state,
    has_substantial_transfer_data,
    start_transfer_session,
    update_conversation_state,
)

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
        actionable_message_repo: ActionableMessageRepository | None = None,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ):
        self.user_cache = user_cache
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.actionable_message_repo = actionable_message_repo
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
            config: RunnableConfig = {"configurable": {"thread_id": f"transfer:{phone_number}"}}
            if self._checkpointer:
                thread_id = config["configurable"]["thread_id"]
                await self._checkpointer.adelete_thread(thread_id)
                logger.info(f"Cleared transfer checkpoint for {phone_number}")
            else:
                logger.warning(
                    f"Checkpointer not initialized, cannot clear checkpoint for {phone_number}"
                )
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
                actionable_message_repo=self.actionable_message_repo,
            ).compile(
                checkpointer=self._checkpointer,
                interrupt_before=[
                    "authorize",
                    "verify_funding",
                ],  # Pause before authorize AND verify_funding
            )

    async def run(
        self,
        phone_number: str,
        message: str,
        message_id: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the transfer flow graph."""
        logger.info(f"run called with message: '{message}'")
        await self._ensure_checkpointer()

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        # Create run context
        config: RunnableConfig = {"configurable": {"thread_id": f"transfer:{phone_number}"}}
        ctx = TransferRunContext(
            phone_number=phone_number,
            message=message,
            message_id=message_id,
            classification_result=classification_result,
            image_data=image_data,
            config=config,
            quoted_data=quoted_data,
        )

        # For repeat/modify transaction intents, clear existing checkpoint to use quoted_data
        intent = ctx.get_classification_intent()
        if intent in ("repeat_transaction", "modify_transaction") and quoted_data:
            logger.info("clearing_checkpoint_for_quote_intent", intent=intent)
            await self.clear_checkpoint(phone_number)

        # Check for cancellation confirmation/decline
        last_response_key = f"user:{phone_number}:last_response"
        last_response = await self.redis_client.get(last_response_key)

        if is_cancellation_confirmation(ctx, last_response or ""):
            return await handle_cancellation_confirmation(ctx, self.graph, self.redis_client)

        if is_cancellation_decline(ctx, last_response or ""):
            return await handle_cancellation_decline_with_checkpoint(ctx, self.graph)

        # Check if this is a flow resume - just replay the last question
        is_flow_resume = (
            classification_result
            and classification_result.get("complexity_reason") == "Flow resume after interrupt"
        )
        if is_flow_resume:
            # User said "yes continue" - get the saved response from when flow was paused
            paused_flow_key = f"user:{phone_number}:paused_flow"
            paused_flow_data = await self.redis_client.get(paused_flow_key)
            if paused_flow_data:
                import json

                paused = json.loads(paused_flow_data)
                saved_response = paused.get("last_response")
                flow_summary = paused.get("flow_summary", {})

                # Clear the paused flow now that we've read it
                await self.redis_client.delete(paused_flow_key)

                if saved_response:
                    # Add contextual prefix
                    amount = flow_summary.get("amount")
                    recipient = flow_summary.get("recipient_name") or flow_summary.get(
                        "recipient_account", ""
                    )

                    if amount and recipient:
                        context_prefix = f"Continuing your ₦{amount:,.0f} transfer to {recipient}! "
                    elif amount:
                        context_prefix = f"Continuing your ₦{amount:,.0f} transfer! "
                    else:
                        context_prefix = "Continuing where you left off! "

                    logger.info("Flow resume detected, replaying saved response with context")
                    return f"{context_prefix}{saved_response}"
            else:
                # Clear just in case (no paused data but is_flow_resume flag was set)
                await self.redis_client.delete(paused_flow_key)

            # If no saved response, check checkpoint state to see if we need to re-send WhatsApp flow
            checkpoint_state = await load_checkpoint_state(ctx, self.graph)
            if checkpoint_state:
                flow_state = checkpoint_state.get("flow_state")
                if flow_state in ("authorizing", "confirming", "confirming_funding"):
                    # User was at authorization - re-send the WhatsApp flow
                    logger.info(
                        f"Flow resume detected, re-sending WhatsApp flow for state: {flow_state}"
                    )

                    # Get the confirmation summary if available
                    confirmation_summary = checkpoint_state.get("confirmation_summary", "")
                    amount = checkpoint_state.get("amount")
                    recipient = checkpoint_state.get("account_resolved", {})
                    recipient_name = (
                        recipient.get("account_name", "") if isinstance(recipient, dict) else ""
                    )

                    # Build a friendly resume message
                    if amount and recipient_name:
                        resume_msg = f"Continuing your ₦{amount:,.0f} transfer to {recipient_name}!"
                    elif amount:
                        resume_msg = f"Continuing your ₦{amount:,.0f} transfer!"
                    else:
                        resume_msg = "Continuing where you left off!"

                    # Send the PIN authorization flow again
                    token = checkpoint_state.get("confirmation_token")
                    if token and self.whatsapp_client:
                        from shared.config import settings

                        await self.whatsapp_client.send_flow(
                            to=phone_number,
                            header="Confirm Your Transfer",
                            flow_cta="Authorize Transfer",
                            flow_id=settings.pin_confirmation_flow_id,
                            screen_name="Pin",
                            flow_token=token,
                            text_body=confirmation_summary or resume_msg,
                        )
                        return resume_msg

                    return (
                        f"{resume_msg}\n\n{confirmation_summary}"
                        if confirmation_summary
                        else resume_msg
                    )

        # Load and prepare state
        input_state = await load_checkpoint_state(ctx, self.graph)

        if input_state:
            input_state = await prepare_checkpoint_state(
                ctx, input_state, self.graph, self.redis_client
            )
            if input_state is None:
                # Non-transfer intent, return empty to let orchestrator handle
                return ""

            # Handle mid-correction during confirming state using LLM extraction
            # SKIP if awaiting_amount_adjustment, confirming_funding, OR is_flow_resume
            flow_state = input_state.get("flow_state")
            if (
                flow_state in ("confirming", "authorizing")
                and flow_state not in ("awaiting_amount_adjustment", "confirming_funding")
                and not is_flow_resume
            ):
                input_state, should_continue = await self._handle_mid_correction(ctx, input_state)
                if not should_continue:
                    return ""
        else:
            input_state = create_initial_state(
                phone_number,
                message,
                message_id,
                classification_result,
                image_data=image_data,
                quoted_data=ctx.quoted_data,
            )

            # CRITICAL FIX: When continuing a session (e.g. valid checkpoint exists),
            # create_initial_state sets fields like recipient_account to None.
            # This overwrites the existing state in the checkpoint.
            # We must filter out None values for business fields to preserve context.
            state_keys_to_preserve = {
                "amount",
                "recipient_name",
                "recipient_account",
                "recipient_bank_code",
                "recipient_bank_name",
                "beneficiaries",
                "accounts",
                "selected_source_account",
                "matched_beneficiary",
                "account_resolved",
                "narration",
                "confirmation_token",
                "confirmation_summary",
            }

            filtered_input = {
                k: v
                for k, v in input_state.items()
                if k not in state_keys_to_preserve or v is not None
            }
            logger.info(
                "run_filtered_input_state",
                filtered_keys=list(filtered_input.keys()),
                amount=filtered_input.get("amount"),
                rec_acct=filtered_input.get("recipient_account"),
                accounts_len=len(filtered_input.get("accounts", [])),
                beneficiaries_len=len(filtered_input.get("beneficiaries", [])),
            )
            input_state = filtered_input

        # Invoke graph
        final_state = await self.graph.ainvoke(cast(TransferState, input_state), config)
        await update_conversation_state(phone_number, cast(TransferState, final_state))

        # Post-processing
        await self._handle_post_processing(phone_number, final_state)

        return final_state.get("response", "")

    async def _handle_mid_correction(
        self,
        ctx: TransferRunContext,
        input_state: dict,
    ) -> tuple[dict, bool]:
        """Handle mid-confirmation corrections using LLM extraction.

        Returns (new_state, should_continue):
            - new_state: Updated state with corrections applied
            - should_continue: True if we should continue processing
        """
        # Preserve existing values
        old_values = {
            "amount": input_state.get("amount"),
            "recipient_account": input_state.get("recipient_account"),
            "recipient_name": input_state.get("recipient_name"),
            "recipient_bank": input_state.get("recipient_bank_name"),
        }

        logger.info("mid_correction_attempt", old_values=old_values, message=ctx.message[:30])

        try:
            extracted = await self.extractor.extract(
                ctx.message,
                ctx.phone_number,
                image_data=ctx.image_data,
            )
        except Exception as e:
            logger.warning("extraction_error", error=str(e))
            return input_state, True

        if not extracted or not extracted.entities:
            return input_state, True

        entities = extracted.entities
        new_values = {
            "amount": entities.amount,
            "recipient_account": entities.recipient_account,
            "recipient_name": entities.recipient_name,
            "recipient_bank": entities.bank_name,
        }

        changes = []
        for key, old_val in old_values.items():
            new_val = new_values.get(key)
            if new_val is not None and new_val != old_val:
                if key == "amount":
                    changes.append(f"amount to ₦{new_val:,.0f}")
                    input_state["amount"] = new_val
                elif key == "recipient_account":
                    changes.append(f"account to {new_val}")
                    input_state["recipient_account"] = new_val
                    input_state["account_resolved"] = None  # Need to re-resolve
                elif key == "recipient_name":
                    changes.append(f"recipient to {new_val}")
                    input_state["recipient_name"] = new_val
                elif key == "recipient_bank":
                    changes.append(f"bank to {new_val}")
                    input_state["recipient_bank_name"] = new_val
                    input_state["recipient_bank_code"] = None  # Will be resolved

        if changes:
            ack_msg = (
                extracted.reply if extracted.reply else f"Got it, changing {' and '.join(changes)}."
            )
            await self.whatsapp_client.send_text(
                ctx.phone_number, ack_msg, message_id=ctx.message_id
            )
            input_state["flow_state"] = "extracting"  # Re-process
            input_state["transfer_status"] = None  # Clear to allow re-confirmation
            logger.info("mid_correction_applied", changes=changes)

        # Update message for re-processing
        input_state["message"] = ctx.message
        input_state["message_id"] = ctx.message_id

        return input_state, True

    async def _handle_post_processing(
        self,
        phone_number: str,
        final_state: dict,
    ) -> None:
        """Handle post-processing after graph execution."""
        transfer_status = final_state.get("transfer_status")

        if has_substantial_transfer_data(cast(TransferState, final_state)):
            await start_transfer_session(phone_number)
        elif transfer_status not in ("pending", None):
            await clear_transfer_session(phone_number)

            if transfer_status in ("completed", "failed", "cancelled", "collection_complete"):
                try:
                    await self.clear_checkpoint(phone_number)
                except Exception as e:
                    logger.warning(f"Failed to clear checkpoint after transfer: {e}")

            if self.completion_callback:
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
                        self.completion_callback.on_flow_complete(phone_number, "transfer", result)
                    )

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, pin_error: str | None = None
    ) -> str:
        """
        Resume graph execution after PIN verification using LangGraph interrupt pattern.

        The graph is compiled with interrupt_before=["authorize"], so after confirm
        sends the PIN flow, the graph pauses at the authorize node. This method:
        1. Updates the state with PIN verification result
        2. Resumes the graph with ainvoke(None, config)
        3. The authorize node then runs and processes the transaction
        """
        await self._ensure_checkpointer()

        config: RunnableConfig = {"configurable": {"thread_id": f"transfer:{phone_number}"}}

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        current_state = await self.graph.aget_state(config)
        if not current_state or not current_state.values:
            return "No active transfer session found."

        # Get the latest message_id from Redis for typing indicators
        current_message_id = await self.redis_client.get(f"user:{phone_number}:current_message_id")
        state_message_id = current_state.values.get("message_id")

        await self.graph.aupdate_state(
            config,
            {
                "pin_verified": pin_verified,
                "pin_verification_error": pin_error,
                "message_id": current_message_id or state_message_id,  # Use fresh ID if available
            },
        )

        logger.info(
            "resume_after_pin_verification_starting",
            phone=phone_number,
            pin_verified=pin_verified,
        )

        # Resume from interrupt - aupdate_state already set pin_verified
        # Pass None to continue from where graph was interrupted (at verify_funding or authorize)
        final_state = await self.graph.ainvoke(None, config)

        # CRITICAL FIX: After PIN verification, the graph routes to "authorize" but ainvoke might return before executing it.
        # This applies to both:
        # 1. Funded transfers: afterdebits complete (flow_state="initiating_payout")
        # 2. Normal transfers: after PIN verified (flow_state="authorizing" with pin_verified=True)
        # Check if we need to continue execution to the authorize node.
        flow_state = final_state.get("flow_state")
        transfer_status = final_state.get("transfer_status")
        pin_verified_state = final_state.get("pin_verified", False)

        # Continue if either:
        # - Funded transfer ready for payout OR
        # - Normal transfer with PIN verified
        should_continue = (
            flow_state == "initiating_payout" and transfer_status != "completed"
        ) or (flow_state == "authorizing" and pin_verified_state and transfer_status != "completed")

        if should_continue:
            logger.info(
                "resume_after_pin_continuing_to_authorize",
                phone=phone_number,
                flow_state=flow_state,
                transfer_status=transfer_status,
            )
            # Continue execution - the graph should now execute the authorize node
            final_state = await self.graph.ainvoke(None, config)

        # Note: ainvoke with input merges input into state
        # We already did aupdate_state but passing it again ensures the run starts

        await update_conversation_state(phone_number, cast(TransferState, final_state))

        response = final_state.get("response", "")
        transfer_status = final_state.get("transfer_status")

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
