"""Conversational response node for chit-chat."""

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from apps.core.src.agent.orchestrator.state import OrchestratorState


class ConversationalNode:
    """Handles conversational messages (greetings, thanks, chit-chat)."""

    def __init__(self, llm: ChatOpenAI):
        """Initialize with LLM."""
        self.llm = llm

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Generate conversational response using LLM."""
        message = state["message"]

        print("💬 Generating conversational response...")

        conversational_prompt = f"""You are a friendly Nigerian banking assistant. A user just sent you a conversational message (not a banking request).

USER MESSAGE: "{message}"

CONTEXT:
- This is chit-chat/social interaction (greeting, thanks, goodbye, small talk, or help request)
- You should respond naturally and warmly in the SAME LANGUAGE the user used
- Keep responses brief (1-2 sentences max)
- If it's a greeting, welcome them and offer help
- If it's thanks, acknowledge politely
- If it's goodbye, wish them well
- If asking about capabilities, mention: balance checks, transfers, airtime/data
- Use appropriate emojis sparingly (👋, 😊, etc.)

Respond naturally and conversationally."""

        try:
            response = await self.llm.ainvoke([HumanMessage(content=conversational_prompt)])

            if hasattr(response, "content") and isinstance(response.content, str):
                state["response"] = response.content.strip()
            else:
                state["response"] = "Hello! How can I help you with your banking today?"
        except Exception as e:
            print(f"⚠️ Error in conversational response: {e}")
            state["response"] = "Hello! I'm your banking assistant. How can I help you today?"

        return state
