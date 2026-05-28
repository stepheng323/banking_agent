"""Select explicit answer strategies for query results."""

from apps.chat.src.agent.workers.query.continuations.messaging import build_soft_clarification
from apps.chat.src.agent.workers.query.models.domain import (
    QueryAnswerContext,
    QueryAnswerStrategy,
    QueryExecutionContract,
    QueryIntent,
    QueryResult,
)
from apps.chat.src.agent.workers.query.presentation.surface_builder import build_focus_referent
from apps.chat.src.agent.workers.query.services.answers.existence import build_existence_answer
from apps.chat.src.agent.workers.query.services.answers.fact_answer import build_direct_fact_answer


def select_answer_strategy(result: QueryResult, *, locale: str = "en") -> QueryResult:
    """Attach an explicit answer strategy to an execution result."""
    if result.answer_strategy is not None:
        return result

    query_contract = result.query_contract

    if query_contract and query_contract.request_shape == "existence":
        return _apply_existence_answer_strategy(result, query_contract=query_contract)

    if query_contract and query_contract.answer_fact_field in {
        "date",
        "counterparty",
        "amount",
        "bank",
        "status",
        "description",
        "reference",
        "account",
        "direction",
        "category",
    }:
        return _apply_fact_answer_strategy(result, query_contract=query_contract, locale=locale)

    if query_contract and query_contract.intent in {
        QueryIntent.ANALYTICS_SUMMARY,
        QueryIntent.BENEFICIARY_SUMMARY,
        QueryIntent.TIME_COMPARISON,
        QueryIntent.AFFORDABILITY,
    }:
        result.answer_strategy = QueryAnswerStrategy.SUMMARY_LIST
        return result

    if result.summary_text and not result.items:
        result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
        result.answer_context = QueryAnswerContext(primary_text=result.summary_text)
        return result

    result.answer_strategy = QueryAnswerStrategy.TRANSACTION_LIST
    return result


def _apply_fact_answer_strategy(result: QueryResult, *, query_contract: QueryExecutionContract, locale: str) -> QueryResult:
    fact_field = query_contract.answer_fact_field or "date"

    if not result.items:
        result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
        return result

    if len(result.items) > 1:
        result.answer_strategy = QueryAnswerStrategy.CLARIFY
        result.answer_context = QueryAnswerContext(
            primary_text=build_soft_clarification(result.items, context="Which transaction", locale=locale)
        )
        return result

    item = result.items[0]
    result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
    result.answer_context = build_direct_fact_answer(
        item,
        query_contract=query_contract,
        fact_field=fact_field,
        locale=locale,
    )
    focus_referent = build_focus_referent(item, query_contract=query_contract)
    if focus_referent is not None:
        result.followup_referent = focus_referent
    return result


def _apply_existence_answer_strategy(
    result: QueryResult,
    *,
    query_contract: QueryExecutionContract,
) -> QueryResult:
    result.answer_strategy = QueryAnswerStrategy.DIRECT_ANSWER
    result.answer_context = QueryAnswerContext(
        primary_text=build_existence_answer(result, query_contract=query_contract)
    )
    return result
