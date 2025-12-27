"""LangGraph graph for airtime purchase flow."""

import asyncio
import json
from typing import TYPE_CHECKING, Any, Optional, cast

if TYPE_CHECKING:
    from apps.core.src.agent.services import FlowCompletionCallback

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from apps.core.src.agent.sub_agents.airtime.extractor import AirtimeEntityExtractor
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState
from apps.core.src.agent.tools.beneficiary.matcher import BeneficiaryMatcher
from shared.cache.redis_client import RedisClient
from shared.cache.user_data import UserDataCache
from shared.clients.whatsapp.client import WhatsAppClient
from shared.config.settings import settings
from shared.queue.redis_queue import RedisQueue
from shared.repositories.account_repository import AccountRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.utils.logging import get_logger

from .builder import build_graph
from .state import create_initial_state, update_conversation_state

logger = get_logger(__name__)

CONTINUATION_PHRASES = {
    "yes",
    "yep",
    "yeah",
    "ok",
    "okay",
    "sure",
    "continue",
    "proceed",
    "go ahead",
    "confirm",
    "yes continue",
    "yes please",
    "go on",
    "that's correct",
    "thats correct",
    "correct",
    "right",
    "yes confirm",
}


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
        actionable_message_repo: ActionableMessageRepository | None = None,
        completion_callback: Optional["FlowCompletionCallback"] = None,
    ):
        self.user_cache = user_cache
        self.account_repo = account_repo
        self.beneficiary_repo = beneficiary_repo
        self.whatsapp_client = whatsapp_client
        self.extractor = extractor
        self.matcher = BeneficiaryMatcher()
        self.actionable_message_repo = actionable_message_repo
        self.completion_callback = completion_callback
        self.redis_client = RedisClient.get_client()
        self.queue = queue
        self.graph = None
        self._checkpointer = None
        self._checkpointer_setup = False

    def _get_config(self, phone_number: str) -> RunnableConfig:
        """Get LangGraph config for a user."""
        return {"configurable": {"thread_id": f"airtime:{phone_number}"}}

    def _preserve_transaction_data(self, state: dict) -> dict[str, Any]:
        """Extract transaction data to preserve from state."""
        return {
            "amount": state.get("amount"),
            "recipient_phone": state.get("recipient_phone"),
            "network": state.get("network"),
            "selected_source_account": state.get("selected_source_account"),
            "recipient_name": state.get("recipient_name"),
            "matched_beneficiary": state.get("matched_beneficiary"),
        }

    async def clear_checkpoint(self, phone_number: str) -> None:
        """Clear airtime flow checkpoint for a user."""
        try:
            await self._ensure_checkpointer()
            if self._checkpointer:
                thread_id = f"airtime:{phone_number}"
                await self._checkpointer.adelete_thread(thread_id)
                logger.info("checkpoint_cleared", phone=phone_number[:6])
        except Exception as e:
            logger.error("checkpoint_clear_error", error=str(e))

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
                actionable_message_repo=self.actionable_message_repo,
            ).compile(
                checkpointer=self._checkpointer,
                interrupt_before=["authorize"],
            )

    async def _handle_flow_resume(self, phone_number: str) -> str | None:
        """Handle flow resume after interrupt - returns replay response or None."""
        paused_flow_key = f"user:{phone_number}:paused_flow"
        paused_flow_data = await self.redis_client.get(paused_flow_key)

        if paused_flow_data:
            paused = json.loads(paused_flow_data)
            saved_response = paused.get("last_response")
            flow_summary = paused.get("flow_summary", {})

            if saved_response:
                amount = flow_summary.get("amount")
                recipient_phone = flow_summary.get("recipient_phone", "")

                if amount and recipient_phone:
                    prefix = f"Continuing your ₦{amount:,.0f} airtime for {recipient_phone}! "
                elif amount:
                    prefix = f"Continuing your ₦{amount:,.0f} airtime purchase! "
                else:
                    prefix = "Continuing where you left off! "

                logger.info("flow_resume_replay", phone=phone_number[:6])
                return f"{prefix}{saved_response}"

        # No paused flow data - the response will come from the LangGraph checkpoint
        return None

    async def _load_state_from_checkpoint(self, config: RunnableConfig) -> tuple[dict | None, bool]:
        """Load state from LangGraph checkpoint. Returns (state, is_stale)."""
        try:
            current_state = await self.graph.aget_state(config)
            if current_state and current_state.values:
                loaded_state = dict(current_state.values)
                flow_state = loaded_state.get("flow_state")
                airtime_status = loaded_state.get("airtime_status")

                # Check if stale
                if flow_state in ("cancelled", "completed", "error") or airtime_status in (
                    "completed",
                    "failed",
                    "cancelled",
                ):
                    return None, True

                logger.info(
                    "checkpoint_loaded", flow_state=flow_state, amount=loaded_state.get("amount")
                )
                return loaded_state, False
        except Exception as e:
            logger.warning("checkpoint_load_error", error=str(e))
        return None, False

    async def _load_state_from_redis(
        self, phone_number: str, message: str, message_id: str, classification_result: dict | None
    ) -> dict | None:
        """Load state from Redis conversation_state as fallback."""
        try:
            conv_state_json = await self.redis_client.get(f"user:{phone_number}:conversation_state")
            if not conv_state_json:
                return None

            conv_state = json.loads(conv_state_json)
            if conv_state.get("active_flow") != "airtime":
                return None
            if conv_state.get("flow_state") not in ("confirming", "authorizing"):
                return None

            # Reconstruct state
            state = create_initial_state(phone_number, message, message_id, classification_result)
            state["flow_state"] = conv_state.get("flow_state", "confirming")
            if conv_state.get("amount"):
                state["amount"] = float(conv_state["amount"])
            if conv_state.get("recipient_phone"):
                state["recipient_phone"] = conv_state["recipient_phone"]
            if conv_state.get("network"):
                state["network"] = conv_state["network"]
            if conv_state.get("selected_source_account"):
                state["selected_source_account"] = conv_state["selected_source_account"]

            logger.info("state_restored_from_redis", flow_state=state.get("flow_state"))
            return state
        except Exception as e:
            logger.warning("redis_state_load_error", error=str(e))
        return None

    async def _handle_mid_correction(
        self,
        phone_number: str,
        message: str,
        message_id: str,
        input_state: dict,
        classification_result: dict | None,
    ) -> tuple[dict | None, bool]:
        """Handle mid-confirmation corrections. Returns (new_state, should_return_early)."""
        from apps.core.src.agent.sub_agents.airtime.nodes.extraction import extract_entities

        preserved = self._preserve_transaction_data(input_state)
        old_values = {k: preserved[k] for k in ("amount", "recipient_phone", "network")}

        logger.info("mid_correction_attempt", old_values=old_values, message=message[:30])
        await self.clear_checkpoint(phone_number)

        # Run extraction
        temp_state = create_initial_state(phone_number, message, message_id, classification_result)
        for k, v in preserved.items():
            if v is not None:
                temp_state[k] = v
        extracted = await extract_entities(temp_state, self.extractor)

        # Check what changed
        new_vals = {k: extracted.get(k) for k in ("amount", "recipient_phone", "network")}
        changed = any(old_values[k] != new_vals[k] and new_vals[k] for k in old_values)

        if not changed:
            # Check continuation intent
            msg_lower = message.lower().strip()
            is_continuation = (
                any(p in msg_lower for p in CONTINUATION_PHRASES)
                or msg_lower in CONTINUATION_PHRASES
            )

            if is_continuation:
                logger.info("continuation_detected", message=message[:20])
                new_state = create_initial_state(
                    phone_number, message, message_id, classification_result
                )
                for k, v in preserved.items():
                    if v is not None:
                        new_state[k] = v
                new_state["flow_state"] = "extracting"
                return new_state, False

            # Ask for clarification
            await self.whatsapp_client.send_text(
                phone_number,
                "I wasn't sure what you wanted to change. Please be specific, e.g., "
                "'Change amount to 2000' or 'Change number to 08123456789'.",
            )
            clarification_state = create_initial_state(
                phone_number, message, message_id, classification_result
            )
            clarification_state["flow_state"] = "confirming"
            for k, v in preserved.items():
                if v is not None:
                    clarification_state[k] = v
            await update_conversation_state(phone_number, cast(AirtimeState, clarification_state))
            return None, True

        # Build specific acknowledgment message
        changes = []
        if old_values["amount"] != new_vals["amount"] and new_vals["amount"]:
            changes.append(f"amount to ₦{new_vals['amount']:,.0f}")
        if (
            old_values["recipient_phone"] != new_vals["recipient_phone"]
            and new_vals["recipient_phone"]
        ):
            changes.append(f"number to {new_vals['recipient_phone']}")
        if old_values["network"] != new_vals["network"] and new_vals["network"]:
            changes.append(f"network to {new_vals['network']}")

        ack_msg = (
            extracted.get("llm_reply")
            if extracted.get("llm_reply")
            else (
                f"Got it, changing {' and '.join(changes)}." if changes else "Got it, updating..."
            )
        )
        await self.whatsapp_client.send_text(phone_number, ack_msg, message_id=message_id)

        new_state = extracted
        for k, v in preserved.items():
            if not new_state.get(k) and v:
                new_state[k] = v
        new_state["flow_state"] = "extracting"
        return new_state, False

    async def _handle_completion(self, phone_number: str, final_state: dict) -> None:
        """Handle flow completion callback."""
        if not self.completion_callback:
            return

        airtime_status = final_state.get("airtime_status")
        if airtime_status in ("completed", "failed", "cancelled"):
            result = {
                "status": airtime_status,
                "amount": final_state.get("amount"),
                "recipient_phone": final_state.get("recipient_phone"),
                "response": final_state.get("response", ""),
            }
            asyncio.create_task(
                self.completion_callback.on_flow_complete(phone_number, "airtime", result)
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
        """Run the airtime purchase flow graph."""
        await self._ensure_checkpointer()
        config = self._get_config(phone_number)

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        # Check flow resume
        is_flow_resume = (
            classification_result
            and classification_result.get("complexity_reason") == "Flow resume after interrupt"
        )
        if is_flow_resume:
            resume_response = await self._handle_flow_resume(phone_number)
            if resume_response:
                return resume_response

        # Load state
        input_state, is_stale = await self._load_state_from_checkpoint(config)
        if is_stale:
            await self.clear_checkpoint(phone_number)
            input_state = None

        if input_state is None:
            input_state = await self._load_state_from_redis(
                phone_number, message, message_id, classification_result
            )

        if input_state is None:
            input_state = create_initial_state(
                phone_number, message, message_id, classification_result, quoted_data=quoted_data
            )
        else:
            # Handle mid-correction or continue
            if input_state.get("flow_state") in ("confirming", "authorizing"):
                corrected, should_return = await self._handle_mid_correction(
                    phone_number, message, message_id, input_state, classification_result
                )
                if should_return:
                    return ""
                input_state = corrected
            else:
                input_state["message"] = message
                input_state["message_id"] = message_id
                input_state["phone_number"] = phone_number
                input_state["response"] = ""
                input_state["llm_reply"] = None
                if classification_result:
                    input_state["classification_result"] = classification_result

        if classification_result and "detected_language" in classification_result:
            input_state["language"] = classification_result["detected_language"]

        # Execute graph
        final_state = await self.graph.ainvoke(cast(AirtimeState, input_state), config)
        await update_conversation_state(phone_number, cast(AirtimeState, final_state))

        logger.info(
            "airtime_flow_completed",
            flow_state=final_state.get("flow_state"),
            airtime_status=final_state.get("airtime_status"),
        )

        await self._handle_completion(phone_number, final_state)
        return final_state.get("response", "")

    async def resume_after_pin_verification(
        self, phone_number: str, pin_verified: bool, pin_error: str | None = None
    ) -> str:
        """Resume graph after PIN verification."""
        await self._ensure_checkpointer()
        config = self._get_config(phone_number)

        if self.graph is None:
            raise RuntimeError("Graph not compiled")

        current_state = await self.graph.aget_state(config)
        if not current_state or not current_state.values:
            return "No active airtime purchase session found."

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

        logger.info("pin_resume_starting", phone=phone_number[:6], pin_verified=pin_verified)

        final_state = await self.graph.ainvoke(None, config)
        await update_conversation_state(phone_number, cast(AirtimeState, final_state))

        logger.info(
            "pin_resume_completed",
            flow_state=final_state.get("flow_state"),
            airtime_status=final_state.get("airtime_status"),
        )

        await self._handle_completion(phone_number, final_state)
        return final_state.get("response", "")
