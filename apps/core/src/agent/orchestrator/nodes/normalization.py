"""Context loading node for user data (accounts, beneficiaries)."""

from apps.core.src.agent.orchestrator.state import OrchestratorState
from apps.core.src.agent.services.user_context_loader import UserContextLoader


class ContextLoaderNode:
    """Loads user context (accounts, beneficiaries, etc.)."""

    def __init__(self, context_loader: UserContextLoader):
        """Initialize with context loader service."""
        self.context_loader = context_loader

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """Load user context from database."""
        phone_number = state["phone_number"]

        print("📚 Loading user context...")
        user_context = await self.context_loader.load_context(phone_number)
        print(f"   ✓ {len(user_context.beneficiaries)} beneficiaries, "
              f"{len(user_context.accounts)} accounts (₦{user_context.get_total_balance():,.2f} total)")

        state["user_context"] = user_context
        return state
