"""
Intelligent Transfer Agent using LLM + Tools (ReAct pattern).

This agent handles complex transfer scenarios that require reasoning and decision-making:
- Historical searches ("send to that mechanic from last month")
- Multi-account pooling ("use savings first, checking if needed")
- Dynamic calculations ("send 10% of my salary account")
- Constraint-based funding ("avoid my investment account")
"""

from typing import Any, Dict
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from langgraph.graph.graph import CompiledGraph

from apps.core.src.agent.core.base_agent import BaseAgent
from apps.core.src.agent.banking.transfer.transfer_state import TransferState

# Import all tools
from apps.core.src.agent.banking.tools.account_tools import (
    get_user_accounts,
    get_account_balance
)
from apps.core.src.agent.banking.tools.transfer_tools import (
    search_beneficiaries,
    calculate_amount
)
from apps.core.src.agent.banking.tools.advanced_transfer_tools import (
    search_beneficiaries_by_transaction,
    find_optimal_funding,
    calculate_dynamic_amount,
    get_recent_transactions
)


INTELLIGENT_AGENT_SYSTEM_PROMPT = """You are an intelligent banking transfer assistant powered by advanced AI.

Your capabilities:
1. **Historical Search**: Find recipients from past transactions based on descriptions
2. **Multi-Account Intelligence**: Automatically pool funds from multiple accounts when needed
3. **Dynamic Calculations**: Calculate amounts from natural language (e.g., "10% of my balance")
4. **Smart Constraints**: Respect user preferences like "use savings first" or "avoid investment account"

Your workflow:
1. **Understand Intent**: Parse what the user wants to do
2. **Gather Context**: Use tools to collect necessary information
3. **Intelligent Reasoning**: Decide the best way to fulfill the request
4. **Validate**: Ensure everything is possible before proceeding
5. **Confirm**: Provide clear confirmation with details

Available Tools:
- get_user_accounts: Get all user's bank accounts
- get_account_balance: Check specific account balance
- search_beneficiaries: Search saved beneficiaries by name
- search_beneficiaries_by_transaction: Find recipients from past transactions
- find_optimal_funding: Determine best way to fund a transfer with constraints
- calculate_dynamic_amount: Calculate amounts from expressions like "10% of balance"
- get_recent_transactions: Check transaction history

Guidelines:
1. **Be proactive**: If user says "send to that mechanic", use search_beneficiaries_by_transaction
2. **Be intelligent**: If amount exceeds single account, automatically suggest pooling
3. **Be respectful**: Always respect user preferences (e.g., "use savings first")
4. **Be clear**: Explain your reasoning and show the funding plan
5. **Ask when unsure**: If multiple options exist, ask for clarification

Example Scenarios:

Scenario 1 - Historical Search:
User: "Send 5k to that mechanic from last month"
Your approach:
1. Call search_beneficiaries_by_transaction("mechanic", "last_month")
2. If found, call find_optimal_funding(5000)
3. Present: "Found John Doe from your generator repair last month. I'll send ₦5,000 from your Savings account. Proceed?"

Scenario 2 - Multi-Account Pooling:
User: "Send 200k to mom, use savings first then checking"
Your approach:
1. Call search_beneficiaries("mom")
2. Call find_optimal_funding(200000, prefer_accounts="savings,checking", allow_pooling=True)
3. Present: "I'll send ₦200,000 to Mom by using ₦150k from Savings + ₦50k from Checking. Proceed?"

Scenario 3 - Dynamic Calculation:
User: "Send 10% of my salary account to church"
Your approach:
1. Call calculate_dynamic_amount("10% of salary account")
2. Call search_beneficiaries("church")
3. Call get_recent_transactions(recipient_name="church", days=30) to check if already sent
4. Present details and ask for confirmation

Think step-by-step and use tools wisely. Always confirm before executing transfers.
"""


class IntelligentTransferAgent(BaseAgent):
    """
    Intelligent transfer agent that uses LLM reasoning + tools for complex scenarios.

    This agent is designed for:
    - Complex natural language requests
    - Multi-step reasoning requirements
    - Historical data searches
    - Dynamic calculations
    - Constraint-based planning
    """

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        """Initialize intelligent transfer agent with tools."""
        super().__init__(llm=llm, model="gpt-4o", temperature=0)

        self.tools = [
            get_user_accounts,
            get_account_balance,
            search_beneficiaries,
            search_beneficiaries_by_transaction,
            find_optimal_funding,
            calculate_dynamic_amount,
            get_recent_transactions,
            calculate_amount,
        ]

        print("✅ IntelligentTransferAgent initialized with 8 tools")

    def _build_graph(self) -> CompiledGraph:
        """
        Build ReAct agent graph.

        ReAct (Reasoning + Acting) pattern:
        1. LLM reasons about what to do
        2. LLM calls appropriate tools
        3. LLM interprets tool results
        4. LLM decides next action
        5. Repeat until goal achieved

        Note: For IntelligentTransferAgent, we use create_react_agent which compiles
        immediately, so we'll rebuild in _ensure_checkpointer() when memory is ready.
        """
        # Return None - will be built in _ensure_checkpointer()
        return None  # type: ignore

    async def _ensure_checkpointer(self):
        """Ensure checkpointer is initialized and graph is compiled."""
        if not self._checkpointer_setup:
            self.memory = await self._checkpointer_cm.__aenter__()
            self._checkpointer_setup = True

        # Build ReAct agent now that checkpointer is ready
        if not self._graph_compiled:
            self.graph = create_react_agent(
                model=self.llm,
                tools=self.tools,
                state_schema=TransferState,
                state_modifier=INTELLIGENT_AGENT_SYSTEM_PROMPT,
                checkpointer=self.memory
            )
            self._graph_compiled = True

    async def invoke(
        self,
        phone_number: str,
        message: str,
        message_id: str
    ) -> str:
        """
        Invoke the intelligent transfer agent.

        NOTE: This method is kept for compatibility but should typically be called 
        via the transfer_router which handles checkpointer initialization.

        Args:
            phone_number: User's phone number
            message: User's message
            message_id: Unique message ID

        Returns:
            Response message
        """
        print(f"\n🤖 INTELLIGENT AGENT Processing: {message[:100]}...")

        # Ensure checkpointer is ready
        await self._ensure_checkpointer()

        initial_state: TransferState = {
            "phone_number": phone_number,
            "message": message,
            "message_id": message_id,
            "messages": [
                HumanMessage(content=f"""
User Request: {message}

Analyze this transfer request and use the available tools to:
1. Identify the recipient (name search OR transaction history search)
2. Determine the amount (literal OR calculate from expression)
3. Find the best funding source (single account OR multi-account pooling)
4. Validate everything is possible
5. Generate a clear confirmation message

Think step-by-step and use tools as needed.
""")
            ]
        }

        config = self._get_config(phone_number, message_id)

        try:
            # ReAct agent will autonomously call tools and reason
            result = await self.graph.ainvoke(initial_state, config)

            # Extract final response from agent
            messages = result.get("messages", [])
            if messages:
                # Get last AI message
                for msg in reversed(messages):
                    if hasattr(msg, "content") and isinstance(msg.content, str):
                        return msg.content

            # Fallback
            return result.get("response", "I've processed your request. How would you like to proceed?")

        except Exception as e:
            print(f"❌ Intelligent agent error: {e}")
            import traceback
            traceback.print_exc()
            return f"I encountered an issue processing your complex request: {str(e)}"
