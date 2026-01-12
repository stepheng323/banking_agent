"""Account management intent handler."""

from typing import TYPE_CHECKING

from apps.core.src.agent.orchestrator.pipeline_stages.intent_routing.handlers.base import IntentHandler

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account_management.service import AccountManagementService
    from apps.core.src.agent.graphs.query.graph import QueryFlowGraph
    from apps.core.src.agent.orchestrator.pipeline.routing_context import RoutingContext


class AccountsHandler(IntentHandler):
    """Handles account management intent routing."""

    def __init__(
        self,
        account_management_service: "AccountManagementService",
        query_graph: "QueryFlowGraph | None" = None,
    ):
        self.account_management_service = account_management_service
        self.query_graph = query_graph

    def can_handle(self, intent: str) -> bool:
        return intent == "manage_accounts"

    @property
    def pausable_flows(self) -> tuple[str, ...]:
        return ("transfer", "airtime")

    @property
    def supports_resume_prompt(self) -> bool:
        return True

    async def handle(self, ctx: "RoutingContext") -> str:
        # Check for active query session - user might be filtering by bank
        if self.query_graph and await self.query_graph.has_active_session(ctx.phone_number):
            query_result = await self.query_graph.run(
                ctx.phone_number,
                ctx.text,
                ctx.user_ctx,
            )
            return query_result if isinstance(query_result, str) else "Query completed."

        return await self.account_management_service.handle_account_management(
            ctx.phone_number,
            ctx.text,
            ctx.user_ctx,
        )
