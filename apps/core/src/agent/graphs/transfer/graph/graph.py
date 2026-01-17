"""LangGraph graph for transfer flow."""

import asyncio
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from apps.core.src.agent.graphs.__shared__.base_flow_graph import BaseFlowGraph
from apps.core.src.agent.graphs.__shared__.beneficiary.matcher import BeneficiaryMatcher
from apps.core.src.agent.graphs.__shared__.validation.service import (
    AsyncValidationService,
)
from apps.core.src.agent.graphs.interfaces import FlowCompletionCallback
from apps.core.src.agent.graphs.transfer.extractor import TransferEntityExtractor
from apps.core.src.agent.graphs.transfer.models import TransferEntities
from apps.core.src.agent.graphs.transfer.resolver import compute_missing_fields
from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.bank_cache import BankCacheService
from shared.cache.user_data import UserDataCache
from shared.clients.factories.payment import PaymentProviderFactory
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import (
    ActionableMessageRepository,
)
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.user_repository import UserRepository
from shared.utils.logging import get_logger
from shared.config import settings

from .builder import build_graph
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

logger = get_logger(__name__)


class TransferFlowGraph(BaseFlowGraph):
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
        completion_callback: FlowCompletionCallback | None = None,
        user_repo: UserRepository | None = None,
    ):
        super().__init__()
        self.user_cache = user_cache
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.actionable_message_repo = actionable_message_repo
        self.completion_callback = completion_callback
        self.user_repo = user_repo

        try:
            provider = PaymentProviderFactory.get_provider_for_service(
                "resolve_account"
            )
        except Exception:
            provider = None
        self.validation_service = AsyncValidationService(provider) if provider else None

        self.bank_cache = BankCacheService(redis_client=self.redis_client)
        self.payment_provider = provider
        self.queue = queue

    @property
    def checkpoint_prefix(self) -> str:
        return "transfer"

    def _build_graph(self) -> CompiledStateGraph:
        """Build and compile the transfer flow graph."""
        return build_graph(
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
            user_repo=self.user_repo,
        ).compile(
            checkpointer=self._checkpointer,
            interrupt_before=[
                "authorize",
                "verify_funding",
            ],
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

        if self._graph is None:
            raise RuntimeError("Graph not compiled")

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
            quoted_data=quoted_data,
        )

        intent = ctx.get_classification_intent()
        if intent in ("repeat_transaction", "modify_transaction") and quoted_data:
            await self.clear_checkpoint(phone_number)

        is_flow_resume = (
            classification_result
            and classification_result.get("complexity_reason")
            == "Flow resume after interrupt"
        )
        if is_flow_resume:
            checkpoint_state = await load_checkpoint_state(ctx, self._graph)
            if checkpoint_state:
                flow_state = checkpoint_state.get("flow_state")
                if flow_state in ("authorizing", "confirming", "confirming_funding"):
                    confirmation_summary = checkpoint_state.get(
                        "confirmation_summary", ""
                    )
                    amount = checkpoint_state.get("amount")
                    recipient = checkpoint_state.get("account_resolved", {})
                    recipient_name = (
                        recipient.get("account_name", "")
                        if isinstance(recipient, dict)
                        else ""
                    )
                    if amount and recipient_name:
                        resume_msg = f"Continuing your ₦{amount:,.0f} transfer to {recipient_name}!"
                    elif amount:
                        resume_msg = f"Continuing your ₦{amount:,.0f} transfer!"
                    else:
                        resume_msg = "Continuing where you left off!"

                    token = checkpoint_state.get("confirmation_token")
                    if token:
                        current_message_id = await self.redis_client.get(
                            f"user:{phone_number}:current_message_id"
                        )

                        await self.whatsapp_client.send_flow(
                            to=phone_number,
                            header="Confirm Your Transfer",
                            flow_cta="Authorize Transfer",
                            flow_id=settings.pin_confirmation_flow_id,
                            screen_name="Pin",
                            flow_token=token,
                            text_body=confirmation_summary or resume_msg,
                            message_id=current_message_id,
                        )
                        return ""
                    return (
                        f"{resume_msg}\n\n{confirmation_summary}"
                        if confirmation_summary
                        else resume_msg
                    )

        input_state = await load_checkpoint_state(ctx, self._graph)
        if input_state:
            input_state = await prepare_checkpoint_state(
                ctx, input_state, self._graph, self.redis_client
            )
            if input_state is None:
                return ""

            flow_state = input_state.get("flow_state")
            if (
                flow_state in ("confirming", "authorizing")
                and flow_state
                not in ("awaiting_amount_adjustment", "confirming_funding")
                and not is_flow_resume
            ):
                input_state, should_continue = await self._handle_mid_correction(
                    ctx, input_state
                )
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
            accounts = filtered_input.get("accounts", [])
            beneficiaries = filtered_input.get("beneficiaries", [])
            accounts_list = accounts if isinstance(accounts, list) else []
            beneficiaries_list = (
                beneficiaries if isinstance(beneficiaries, list) else []
            )
            logger.info(
                "run_filtered_input_state",
                filtered_keys=list(filtered_input.keys()),
                amount=filtered_input.get("amount"),
                rec_acct=filtered_input.get("recipient_account"),
                accounts_len=len(accounts_list),
                beneficiaries_len=len(beneficiaries_list),
            )
            input_state = filtered_input

        final_state = await self._graph.ainvoke(cast(TransferState, input_state), config)
        await update_conversation_state(phone_number, cast(TransferState, final_state))

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
        old_values = {
            "amount": input_state.get("amount"),
            "recipient_account": input_state.get("recipient_account"),
            "recipient_name": input_state.get("recipient_name"),
            "recipient_bank": input_state.get("recipient_bank_name"),
            "narration": input_state.get("narration"),
        }
        try:
            extracted = await self.extractor.extract(
                ctx.message,
                smart_context=None,
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
            "narration": entities.narration,
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
                    input_state["account_resolved"] = None
                elif key == "recipient_name":
                    changes.append(f"recipient to {new_val}")
                    input_state["recipient_name"] = new_val
                elif key == "recipient_bank":
                    changes.append(f"bank to {new_val}")
                    input_state["recipient_bank_name"] = new_val
                    input_state["recipient_bank_code"] = None
                elif key == "narration":
                    changes.append(f"narration to '{new_val}'")
                    input_state["narration"] = new_val

        if changes:
            ack_msg = f"Got it, changing {' and '.join(changes)}."
            await self.whatsapp_client.send_text(
                ctx.phone_number, ack_msg, message_id=ctx.message_id
            )
            
            # Recompute missing fields to ensure routing works correctly
            current_entities = TransferEntities(
                amount=input_state.get("amount"),
                recipient_account=input_state.get("recipient_account"),
                recipient_name=input_state.get("recipient_name"),
                bank_name=input_state.get("recipient_bank_name"),
                bank_code=input_state.get("recipient_bank_code"),
                source_bank_name=input_state.get("source_bank_name"),
                transfer_all=input_state.get("transfer_all"),
                transfer_percentage=input_state.get("transfer_percentage"),
            )
            is_internal = bool(
                current_entities.source_bank_name 
                and current_entities.bank_name 
                and not current_entities.recipient_name
            )
            input_state["missing_fields"] = compute_missing_fields(current_entities, is_internal)
            
            # Skip extraction and go straight to validation since we applied changes directly
            input_state["flow_state"] = "validating"
            input_state["transfer_status"] = None
            logger.info("mid_correction_applied", changes=changes, missing=input_state["missing_fields"])

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

            if transfer_status in (
                "authorized",
                "completed",
                "failed",
                "cancelled",
                "collection_complete",
            ):
                try:
                    await self.clear_checkpoint(phone_number)
                    paused_flow_key = f"user:{phone_number}:paused_flow"
                    await self.redis_client.delete(paused_flow_key)
                except Exception as e:
                    logger.warning(f"Failed to clear checkpoint after transfer: {e}")

            if self.completion_callback:
                if transfer_status in (
                    "authorized",
                    "completed",
                    "failed",
                    "cancelled",
                    "collection_complete",
                ):
                    result = {
                        "status": transfer_status,
                        "amount": final_state.get("amount"),
                        "recipient_account": final_state.get("recipient_account"),
                        "recipient_bank_name": final_state.get("recipient_bank_name"),
                        "recipient_bank_code": final_state.get("recipient_bank_code"),
                        "account_resolved": final_state.get("account_resolved"),
                        "selected_source_account": final_state.get(
                            "selected_source_account"
                        ),
                        "response": final_state.get("response", ""),
                    }
                    asyncio.create_task(
                        self.completion_callback.on_flow_complete(
                            phone_number, "transfer", result
                        )
                    )

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, pin_error: str | None = None
    ) -> str:
        """
        Resume graph execution after PIN verification using LangGraph interrupt pattern.
        """
        final_state = await self._inject_pin_and_resume(
            phone_number, pin_verified, pin_error
        )

        if not final_state:
            return "No active transfer session found."
        
        flow_state = final_state.get("flow_state")
        transfer_status = final_state.get("transfer_status")

        await update_conversation_state(phone_number, cast(TransferState, final_state))

        response = final_state.get("response", "")

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
