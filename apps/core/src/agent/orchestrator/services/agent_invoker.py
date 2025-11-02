"""Service for invoking specialized agents."""

from typing import Any, Dict

from apps.core.src.agent.banking.query.query_agent import QueryAgent
from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent
from apps.core.src.agent.utility.utility_agent import UtilityAgent
from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.core.state import AgentState
from apps.core.src.agent.banking.transfer.transfer_state import TransferState
from apps.core.src.agent.utility.utility_state import UtilityState


class AgentInvoker:
    """Handles invocation of specialized agent subgraphs."""

    def __init__(
        self,
        query_agent: QueryAgent,
        transfer_agent: TransferAgent,
        utility_agent: UtilityAgent,
    ):
        """Initialize with specialized agents."""
        self.query_agent = query_agent
        self.transfer_agent = transfer_agent
        self.utility_agent = utility_agent

    async def invoke_query_agent(
        self,
        *,
        phone_number: str,
        instruction: str,
        message_id: str,
        context: ConversationContext,
    ) -> Dict[str, Any]:
        """Execute the query agent with shared context management."""
        if context.active_agent != "query":
            context.switch_agent("query")

        try:
            config = {"configurable": {"thread_id": phone_number}}
            existing_state = await self.query_agent.graph.aget_state(config)

            query_state: AgentState = {
                "phone_number": phone_number,
                "message": instruction,
                "message_id": message_id,
            }

            if existing_state and existing_state.values:
                existing_values = existing_state.values
                if "messages" in existing_values:
                    query_state["messages"] = existing_values["messages"]
                for key in ["user_id", "accounts", "balance", "selected_account"]:
                    if key in existing_values:
                        query_state[key] = existing_values[key]  # type: ignore

            result = await self.query_agent.graph.ainvoke(query_state, config)

            response = result.get(
                "response", "I'm sorry, I couldn't process your request.")
            awaiting_clarification = result.get("awaiting_clarification")
            clarification_type = result.get("clarification_type")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
            else:
                context.clear_awaiting_clarification()

            return {
                "response": response,
                "result_state": result,
                "awaiting_clarification": awaiting_clarification,
                "clarification_type": clarification_type,
            }

        except Exception as exc:
            context.clear_awaiting_clarification()
            error_message = "I'm sorry, an error occurred while processing your query."
            print(f"❌ Query agent error: {exc}")
            return {
                "response": error_message,
                "error": str(exc),
                "result_state": {},
            }

    async def invoke_transfer_agent(
        self,
        *,
        phone_number: str,
        instruction: str,
        message_id: str,
        context: ConversationContext,
    ) -> Dict[str, Any]:
        """Execute the transfer agent with shared context management."""
        # Only switch if actually changing agents - this preserves awaiting_clarification
        # when continuing a conversation with the same agent
        if context.active_agent != "transfer":
            context.switch_agent("transfer")
        else:
            # Still update activity timestamp even if not switching
            context.update_activity()

        try:
            config = {"configurable": {"thread_id": phone_number}}
            existing_state = await self.transfer_agent.graph.aget_state(config)

            transfer_state: TransferState = {
                "phone_number": phone_number,
                "message": instruction,
                "message_id": message_id,
            }

            # Restore state from checkpoint first
            if existing_state and existing_state.values:
                existing_values = existing_state.values
                for key in [
                    "messages",
                    "transfer_details",
                    "user_accounts",
                    "user_beneficiaries",
                    "conversation_stage",
                    "missing_slots",
                    "pending_clarification",  # CRITICAL: Must restore this for routing
                    "clarifications_needed",
                    "execution_plan",
                    "validation_result",
                    "dependencies",
                ]:
                    if key in existing_values:
                        # type: ignore
                        transfer_state[key] = existing_values[key]

            # Set clarification flags from context (context takes precedence over checkpoint)
            # This ensures the latest continuation state is used
            if context.awaiting_clarification:
                transfer_state["awaiting_clarification"] = True
                if context.clarification_type:
                    transfer_state["clarification_type"] = context.clarification_type
            elif "awaiting_clarification" in transfer_state:
                # If context says not awaiting, clear it
                transfer_state["awaiting_clarification"] = False
                transfer_state["clarification_type"] = None

            # Debug: Check routing conditions
            print(f"🔍 TRANSFER AGENT INVOCATION:")
            print(f"   Message: {instruction[:50]}...")
            print(f"   awaiting_clarification: {transfer_state.get('awaiting_clarification')}")
            print(f"   pending_clarification: {transfer_state.get('pending_clarification')}")
            print(f"   clarification_type: {transfer_state.get('clarification_type')}")

            result = await self.transfer_agent.graph.ainvoke(transfer_state, config)

            response = result.get(
                "response", "I'm sorry, I couldn't process your transfer request.")
            awaiting_clarification = result.get("awaiting_clarification")
            clarification_type = result.get("clarification_type")
            conversation_stage = result.get("conversation_stage")

            print(f"📤 TRANSFER AGENT RESULT:")
            print(f"   awaiting_clarification: {awaiting_clarification}")
            print(f"   clarification_type: {clarification_type}")
            print(f"   conversation_stage: {conversation_stage}")
            print(f"   Context BEFORE update: awaiting={context.awaiting_clarification}, agent={context.active_agent}")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
                print(f"   ✅ Context UPDATED: awaiting={context.awaiting_clarification}, type={context.clarification_type}")
            elif conversation_stage == "completed":
                context.clear_awaiting_clarification()
                print(f"   ✅ Context CLEARED: awaiting={context.awaiting_clarification}")

            return {
                "response": response,
                "result_state": result,
                "awaiting_clarification": awaiting_clarification,
                "clarification_type": clarification_type,
                "conversation_stage": conversation_stage,
            }

        except Exception as exc:  # pragma: no cover - defensive
            context.clear_awaiting_clarification()
            error_message = "I'm sorry, an error occurred while processing your transfer."
            print(f"❌ Transfer agent error: {exc}")
            return {
                "response": error_message,
                "error": str(exc),
                "result_state": {},
            }

    async def invoke_utility_agent(
        self,
        *,
        phone_number: str,
        instruction: str,
        message_id: str,
        context: ConversationContext,
    ) -> Dict[str, Any]:
        """Execute the utility agent with shared context management."""
        if context.active_agent != "utility":
            context.switch_agent("utility")

        try:
            config = {"configurable": {"thread_id": phone_number}}
            existing_state = await self.utility_agent.graph.aget_state(config)

            utility_state: UtilityState = {
                "phone_number": phone_number,
                "message": instruction,
                "message_id": message_id,
            }

            if existing_state and existing_state.values:
                existing_values = existing_state.values
                if "messages" in existing_values:
                    utility_state["messages"] = existing_values["messages"]
                for key in ["utility_type", "amount", "recipient_phone", "network_provider",
                            "source_account_id", "missing_slots", "conversation_stage"]:
                    if key in existing_values:
                        # type: ignore
                        utility_state[key] = existing_values[key]

            result = await self.utility_agent.graph.ainvoke(utility_state, config)

            response = result.get(
                "response", "I'm sorry, I couldn't process your utility request.")
            awaiting_clarification = result.get("awaiting_clarification")
            clarification_type = result.get("clarification_type")
            conversation_stage = result.get("conversation_stage")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
            elif conversation_stage == "completed":
                context.clear_awaiting_clarification()

            return {
                "response": response,
                "result_state": result,
                "awaiting_clarification": awaiting_clarification,
                "clarification_type": clarification_type,
                "conversation_stage": conversation_stage,
            }

        except Exception as exc:
            context.clear_awaiting_clarification()
            error_message = "I'm sorry, an error occurred while processing your utility request."
            print(f"❌ Utility agent error: {exc}")
            return {
                "response": error_message,
                "error": str(exc),
                "result_state": {},
            }
