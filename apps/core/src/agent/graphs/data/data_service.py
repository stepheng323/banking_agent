"""Data service wrapper for quote-based transactions."""

from apps.core.src.agent.graphs.data.graph import DataPurchaseGraph
from apps.core.src.agent.graphs.interfaces import ITransactionService


class DataService(ITransactionService):
    """Wrapper around DataPurchaseGraph to implement ITransactionService interface."""

    def __init__(self, graph: DataPurchaseGraph):
        self.graph = graph

    async def run_simple(
        self,
        phone: str,
        text: str,
        classification_result: dict | None = None,
        image_data: str | None = None,
        quoted_data: dict | None = None,
    ) -> str:
        """Run the data purchase flow.

        Args:
            phone: User's phone number
            text: User's message
            classification_result: Classification result from orchestrator
            image_data: Optional base64 image data (not used for data)
            quoted_data: Data from quoted transaction (for repeat/modify)
        """
        user_context = {}

        if quoted_data:
            data = quoted_data.get("data", {})
            user_context["quoted_phone"] = data.get("phone_number")
            user_context["quoted_network"] = data.get("network")
            user_context["quoted_plan_name"] = data.get("plan_name")
            user_context["quoted_amount"] = data.get("amount")

        return await self.graph.run(
            phone_number=phone,
            message=text,
            user_context=user_context,
        )
