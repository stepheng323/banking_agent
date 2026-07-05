"""Generative formatting step for the query pipeline.

Rewrites the deterministic i18n query response using an LLM to
provide a fluid, conversational answer to the user's raw query,
while preserving data integrity.
"""

from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from banking.presentation.i18n.locale import LocaleManager
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.models.domain import QueryResult
from banking.transactions.query.pipeline import QueryStep
from banking.transactions.query.presentation.formatter import QueryFormatter
from shared.utils.logging import get_logger

logger = get_logger(__name__)

PROMPT_TEMPLATE = """You are a helpful financial assistant for a banking application.

The user asked the following question:
<user_query>
{user_query}
</user_query>

The system retrieved the data and generated the following deterministic response:
<system_response>
{system_response}
</system_response>

Your task is to re-write the system response so that it fluidly and naturally answers the user's query.

Constraints:
1. If the system response contains a list of items or a structured summary (e.g. multiple lines with bullet points or
    dashes), KEEP the list structure exactly as is, but rewrite the introductory sentence to be more natural and
    directly address the user's specific query.
2. If the system response is a direct fact (e.g. a date and an amount on separate lines), synthesize them into a
    single fluid sentence that directly answers the user's question (e.g., "The last time you paid X was on date,
    and you sent them Y.").
3. DO NOT change any numbers, dates, names, or financial facts.
4. DO NOT add conversational fluff like "Hello!" or "I can help with that." Just output the final response text.
5. Respond in the following language/locale: {locale}.
"""

class GenerativeFormattingStep(QueryStep):
    """Rewrites the deterministic query response to be more conversational."""

    def __init__(self, llm: Runnable) -> None:
        self.llm = llm
        self.chain: Runnable | None = None
        try:
            self.chain = (
                ChatPromptTemplate.from_messages([("system", PROMPT_TEMPLATE)])
                | self.llm
                | StrOutputParser()
            )
        except Exception:
            self.chain = None

    async def run(self, state: dict[str, Any], worker_context: Any = None) -> TransactionResult:
        """Run the generative formatting logic."""
        if not self.chain:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        flow_state = state.get("flow_state")
        if flow_state != "complete":
            # If the pipeline didn't complete successfully, don't try to rewrite it.
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        query_result_raw = state.get("query_result")
        if isinstance(query_result_raw, dict):
            try:
                query_result = QueryResult.model_validate(query_result_raw)
            except Exception:
                return TransactionResult(outcome=TransactionOutcome.OK, patch={})
        elif isinstance(query_result_raw, QueryResult):
            query_result = query_result_raw
        else:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        user_query = state.get("message", "").strip()
        if not user_query:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        locale = LocaleManager.normalize(state.get("language")).value
        current_page = state.get("current_page", 0)
        show_expanded = state.get("show_expanded", False)

        # Generate the deterministic fallback response as the baseline
        system_response = QueryFormatter.format(
            query_result,
            current_page=current_page,
            show_expanded=show_expanded,
            has_more=query_result.has_more,
            locale=locale,
        )

        try:
            llm_response = await self.chain.ainvoke(
                {
                    "user_query": user_query,
                    "system_response": system_response,
                    "locale": locale,
                }
            )
            llm_response = llm_response.strip()

            if state.get("resolver_message"):
                llm_response = f"_{state['resolver_message']}_\n\n{llm_response}"

            logger.info("generative_formatting_applied", user_query=user_query)

            # We return a TransactionResult with the NEW response string.
            # We don't need to patch the state, just override the response output.
            return TransactionResult(
                outcome=TransactionOutcome.OK,
                response=llm_response,
                patch={},
            )
        except Exception as e:
            logger.warning("generative_formatting_failed", error=str(e), exc_info=True)
            # If the LLM fails, we just return empty patch and the pipeline keeps the fallback.
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})
