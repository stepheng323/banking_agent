"""Static prompt/schema budget guards for interactive LLM roles."""

from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_llm import SemanticRouteLLMDecision
from apps.chat.src.agent.orchestrator.workflows.gate.utils.semantic_router_prompt_compiler import (
    SemanticRouterPromptSignals,
    compile_semantic_router_prompt,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_llm_models import (
    PlannerKnownAirtimeDataPlan,
    PlannerKnownAirtimePlan,
    PlannerKnownDataPlan,
    PlannerKnownTransactionsPlan,
    PlannerKnownTransferAirtimePlan,
    PlannerKnownTransferDataPlan,
    PlannerKnownTransferPlan,
)
from banking.transactions.query.services.reasoning.prompt_compiler import compile_query_reasoner_prompt
from banking.transfers.models.amendment import TransferAmendmentPatch
from shared.observability.llm_call_metrics import estimated_tokens_from_chars, response_schema_metrics


def test_semantic_router_compiler_selects_only_state_atoms_within_prompt_budget() -> None:
    base = compile_semantic_router_prompt(SemanticRouterPromptSignals())
    active = compile_semantic_router_prompt(
        SemanticRouterPromptSignals(active_query=True, active_flow=True, context_frame=True, schedule_context=True)
    )

    assert "Active-query atom" not in base.system_prompt
    assert "Active-query atom" in active.system_prompt
    assert active.profile == "query+flow+frame+schedule"
    assert active.cache_key != base.cache_key
    assert estimated_tokens_from_chars(len(active.system_prompt)) <= 1600
    assert response_schema_metrics(SemanticRouteLLMDecision)["response_schema_token_estimate"] <= 750


def test_known_planner_schemas_fit_role_budget() -> None:
    schemas = (
        PlannerKnownTransferPlan,
        PlannerKnownAirtimePlan,
        PlannerKnownDataPlan,
        PlannerKnownTransferAirtimePlan,
        PlannerKnownTransferDataPlan,
        PlannerKnownAirtimeDataPlan,
        PlannerKnownTransactionsPlan,
    )

    assert all(response_schema_metrics(schema)["response_schema_token_estimate"] <= 1000 for schema in schemas)


def test_query_prompt_profiles_and_transfer_amendment_schema_fit_budgets() -> None:
    query_profiles = (
        "focused_item",
        "transaction_list",
        "grouped_summary",
        "historical_frames",
        "pending_clarification",
    )

    assert all(
        estimated_tokens_from_chars(len(compile_query_reasoner_prompt(profile).system_prompt)) <= 1800
        for profile in query_profiles
    )
    assert response_schema_metrics(TransferAmendmentPatch)["response_schema_token_estimate"] <= 700
