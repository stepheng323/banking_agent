# ruff: noqa
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeStubs=false
"""Query Agent - Handles balance checks and account information queries."""

from typing import Any
import json

from langchain_openai import ChatOpenAI
from langgraph.graph.state import StateGraph
from langgraph.graph.graph import END
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from apps.core.src.agent.core.base_agent import BaseAgent
from apps.core.src.agent.core.state import AgentState
from apps.core.src.agent.banking.tools.account_tools import (
    get_user_accounts,
    get_account_balance,
)


class QueryAgent(BaseAgent):
    """
    Query Agent for handling simple banking queries:
    - Balance checks
    - Account information
    - Account listing
    """

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        super().__init__(llm=llm, model="gpt-4o-mini", temperature=0)
        self.query_tools = [get_user_accounts, get_account_balance]

    def _build_graph(self) -> Any:
        """Build the query agent graph."""
        graph = StateGraph(AgentState)

        graph.add_node("llm", self._call_llm)
        graph.add_node("tools", self._tools_node)

        graph.set_entry_point("llm")
        graph.add_conditional_edges(
            "llm",
            self._should_use_tools,
            {
                "tools": "tools",
                "end": END,
            },
        )
        graph.add_edge("tools", "llm")
        return graph.compile(checkpointer=self.memory)

    async def _call_llm(self, state: AgentState) -> AgentState:
        """Call LLM with conversation for queries."""
        messages = state.get("messages", []) or []
        phone_number = state.get("phone_number", "")

        if not messages:
            system_msg = SystemMessage(
                content=(
                    "You are a helpful banking assistant. Use tools to check balances and account information. "
                    f"User phone: {phone_number}."
                )
            )
            messages.append(system_msg)

        user_msg = HumanMessage(content=state["message"])
        messages.append(user_msg)

        llm_with_tools = self.llm.bind_tools(self.query_tools)
        response = await llm_with_tools.ainvoke(messages)

        state["messages"] = messages + [response]
        if hasattr(response, "content") and isinstance(response.content, str):
            state["response"] = response.content
        return state

    async def _tools_node(self, state: AgentState) -> AgentState:
        """Invoke tools for queries."""
        messages = state.get("messages", []) or []
        if not messages:
            return state

        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            for tool_call in last_message.tool_calls:
                tool_name = getattr(tool_call, "name", None)
                tool_call_id = getattr(tool_call, "id", None)
                args = getattr(tool_call, "args", None)

                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                elif args is None:
                    args = {}

                if tool_name == "get_user_accounts":
                    result = get_user_accounts(**args)
                elif tool_name == "get_account_balance":
                    result = get_account_balance(**args)
                else:
                    result = {"error": f"Unknown tool {tool_name}"}

                tool_message = ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=tool_call_id or tool_name or "unknown",
                )
                messages.append(tool_message)

            # Get final response with tool results
            final_response = await self.llm.ainvoke(messages)
            if hasattr(final_response, "content") and isinstance(final_response.content, str):
                state["response"] = final_response.content

            state["messages"] = messages

        return state

    def _should_use_tools(self, state: AgentState) -> str:
        """Determine if tools should be used."""
        messages = state.get("messages", []) or []
        if not messages:
            return "end"
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return "end"

    async def invoke(self, phone_number: str, message: str, message_id: str) -> str:
        """Invoke the query agent."""
        initial_state: AgentState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "messages": [],
        }
        config = self._get_config(phone_number, message_id)
        result = await self.graph.ainvoke(initial_state, config)
        return result.get("response", "I'm sorry, I couldn't process your request.")
