"""Service for invoking specialized agents."""

from typing import Any, Dict, List

from apps.core.src.agent.banking.query.query_agent import QueryAgent
from apps.core.src.agent.banking.transfer.transfer_router import route_transfer_request
from apps.core.src.agent.utility.utility_agent import UtilityAgent
from apps.core.src.agent.utility.utility_state import UtilityState
from apps.core.src.agent.conversation_context import ConversationContext
from apps.core.src.agent.core.state import AgentState
from shared.clients.whatsapp_client import WhatsAppClient


class AgentInvoker:
    """Handles invocation of specialized agent subgraphs."""

    def __init__(
        self,
        query_agent: QueryAgent,
        utility_agent: UtilityAgent,
        whatsapp_client: WhatsAppClient,
    ):
        """
        Initialize with specialized agents.

        Note: TransferAgent routing is handled by transfer_router which automatically
        selects between SimpleTransferAgent and IntelligentTransferAgent.
        """
        self.query_agent = query_agent
        self.utility_agent = utility_agent
        self.whatsapp_client = whatsapp_client

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
            await self.query_agent._ensure_checkpointer()

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
        """
        Execute the transfer agent with shared context management.

        Uses intelligent routing to select between Simple and Intelligent agents.
        """

        if context.active_agent != "transfer":
            context.switch_agent("transfer")
        else:
            context.update_activity()

        try:
            print("""🔍 TRANSFER AGENT INVOCATION:""")
            print(f"   Message: {instruction[:50]}...")
            print(
                f"   Context awaiting_clarification: {context.awaiting_clarification}")
            print(
                f"   Context clarification_type: {context.clarification_type}")

            result = await route_transfer_request(phone_number, instruction, message_id)

            response = result["response"]
            awaiting_clarification = result.get(
                "awaiting_clarification", False)
            clarification_type = result.get("clarification_type")
            conversation_stage = result.get("conversation_stage", "completed")

            print("""📤 TRANSFER AGENT RESULT:""")
            print(f"   Response: {response[:100]}...")
            print(f"   awaiting_clarification: {awaiting_clarification}")
            print(f"   clarification_type: {clarification_type}")
            print(f"   conversation_stage: {conversation_stage}")

            outbox: List[Dict[str, Any]] = result.get(
                "outbox_messages") or []  # type: ignore[assignment]
            for msg in outbox:
                try:
                    if msg.get("channel") == "whatsapp" and msg.get("type") == "flow":
                        flow = msg["flow"]
                        # CRITICAL: 'to' is at the message level, not in flow dict
                        to_number = msg.get("to") or phone_number
                        if not to_number:
                            print(
                                f"⚠️  No 'to' field in message and phone_number is None, skipping flow")
                            continue
                        await self.whatsapp_client.send_flow(
                            to=to_number,
                            flow_id=flow.get("flow_id"),
                            flow_cta=flow.get("flow_cta"),
                            screen_name=flow.get("screen_name"),
                            header=flow.get("header"),
                            text_body=flow.get("text_body"),
                            footer=flow.get("footer"),
                            flow_token=flow.get("flow_token"),
                            flow_action_payload=flow.get(
                                "flow_action_payload", {})
                        )
                except Exception as send_exc:
                    print(f"⚠️  Failed to dispatch outbox message: {send_exc}")

            if awaiting_clarification:
                context.set_awaiting_clarification(clarification_type)
                print(
                    f"   ✅ Context UPDATED: awaiting={context.awaiting_clarification}, type={context.clarification_type}")
            elif conversation_stage == "completed":
                context.clear_awaiting_clarification()
                print(
                    f"   ✅ Context CLEARED: awaiting={context.awaiting_clarification}")

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
            import traceback
            traceback.print_exc()
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
            # Ensure checkpointer is initialized before accessing graph
            await self.utility_agent._ensure_checkpointer()

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
