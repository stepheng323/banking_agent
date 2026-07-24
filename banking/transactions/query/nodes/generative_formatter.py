"""Guarded prose polish for short, direct query answers.

Structured query surfaces are already built from localized presentation plans.
They must remain deterministic so rows, totals, and pagination never pay for
an LLM rewrite or risk presentation drift.  This step is retained only for a
single direct answer where a concise natural sentence adds user-visible value.
"""

import re
from time import perf_counter
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

from banking.presentation.i18n.locale import LocaleManager
from banking.runtime.results import TransactionOutcome, TransactionResult
from banking.transactions.query.models.domain import QueryAnswerStrategy, QueryResult
from banking.transactions.query.pipeline import QueryStep
from banking.transactions.query.presentation.formatter import QueryFormatter
from shared.observability.llm_call_metrics import record_llm_call
from shared.observability.llm_http import start_llm_http_recording, stop_llm_http_recording, summarize_llm_http_records
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_AMOUNT_RE = re.compile(r"₦\s*\d[\d,]*(?:\.\d+)?")
_COUNT_RE = re.compile(r"\b\d[\d,]*\s+(?:txn|txns|transaction|transactions|transfer|transfers)\b", re.I)
_MONTH_DATE_RE = re.compile(
    r"\b(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?\b",
    re.I,
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_REFERENCE_RE = re.compile(r"\b(?:txn|ref)[_-]?[A-Za-z0-9_-]+\b", re.I)
_MASKED_ACCOUNT_RE = re.compile(r"(?:\*|·|•){2,}\d{3,4}\b")
_NAMED_ENTITY_RE = re.compile(r"\b(?:[A-Z][A-Za-z]*|[A-Z]{2,})(?:\s+(?:[A-Z][A-Za-z]*|[A-Z]{2,})){0,3}\b")
_STRUCTURED_ROW_MARKERS = ("₦", "—", "·")
_GENERIC_ENTITY_WORDS = frozenset(
    {"Amount", "Bank", "Category", "Date", "Description", "Details", "Ref", "Status", "The", "Transaction", "Type"}
)

PROMPT_TEMPLATE = """You are a helpful financial assistant for a banking application.

The user asked the following question:
<user_query>
{user_query}
</user_query>

The system retrieved the data and generated the following deterministic response:
<system_response>
{system_response}
</system_response>

Your task is to re-write this short direct answer so it fluidly and naturally answers the user's query.

Constraints:
1. The system response is a direct fact (e.g. a date and an amount on separate lines); synthesize it into a
    single fluid sentence that directly answers the user's question (e.g., "The last time you paid X was on date,
    and you sent them Y.").
2. DO NOT change any numbers, dates, names, or financial facts.
3. DO NOT add conversational fluff like "Hello!" or "I can help with that." Just output the final response text.
4. Respond in the following language/locale: {locale}.
"""


def _normalize_numeric_text(value: str) -> str:
    return re.sub(r"[,\s]", "", value).lower()


def _normalize_amount(value: str) -> str:
    return f"amount:{_normalize_numeric_text(value.replace('₦', ''))}"


def _normalize_count(value: str) -> str:
    match = re.search(r"\d[\d,]*", value)
    if match is None:
        return ""
    return f"count:{_normalize_numeric_text(match.group(0))}"


def _normalize_month_date(match: re.Match[str]) -> str:
    month = match.group("month")[:3].lower()
    day = str(int(match.group("day")))
    year = match.group("year") or ""
    return f"date:{month}:{day}:{year}"


def _normalize_iso_date(value: str) -> str:
    return f"date:{value.lower()}"


def _normalize_reference(value: str) -> str:
    return f"ref:{value.strip().lower()}"


def _normalize_masked_account(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"acct:{digits}"


def _extract_fact_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    tokens.update(_normalize_amount(match.group(0)) for match in _AMOUNT_RE.finditer(text))
    tokens.update(_normalize_count(match.group(0)) for match in _COUNT_RE.finditer(text))
    tokens.update(_normalize_month_date(match) for match in _MONTH_DATE_RE.finditer(text))
    tokens.update(_normalize_iso_date(match.group(0)) for match in _ISO_DATE_RE.finditer(text))
    tokens.update(_normalize_reference(match.group(0)) for match in _REFERENCE_RE.finditer(text))
    tokens.update(_normalize_masked_account(match.group(0)) for match in _MASKED_ACCOUNT_RE.finditer(text))
    return {token for token in tokens if token}


def _preserves_fact_tokens(*, source: str, candidate: str) -> bool:
    source_tokens = _extract_fact_tokens(source)
    if not source_tokens:
        return True
    candidate_tokens = _extract_fact_tokens(candidate)
    return source_tokens.issubset(candidate_tokens)


def _named_entities(text: str) -> set[str]:
    return {
        " ".join(match.group(0).casefold().split())
        for match in _NAMED_ENTITY_RE.finditer(text)
        if match.group(0) not in _GENERIC_ENTITY_WORDS
    }


def _preserves_named_entities(*, source: str, candidate: str) -> bool:
    """Keep direct-answer counterparties and bank labels immutable too."""
    return _named_entities(source).issubset(_named_entities(candidate))


def _normalize_structured_row(value: str) -> str:
    row = value.strip()
    row = re.sub(r"^[*_`~\s]*[•*-]\s*", "", row)
    row = row.strip("*_`~ ")
    row = re.sub(r"\s+", " ", row)
    return row.lower()


def _is_structured_row(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.lower().startswith(("more for next page", "total:", "*total:")):
        return False
    if stripped.startswith(("•", "- ")) and any(marker in stripped for marker in _STRUCTURED_ROW_MARKERS):
        return True
    return "₦" in stripped and any(marker in stripped for marker in ("—", "·", "%"))


def _extract_structured_rows(text: str) -> list[str]:
    rows = [_normalize_structured_row(line) for line in text.splitlines() if _is_structured_row(line)]
    return [row for row in rows if row]


def _preserves_structured_rows(*, source: str, candidate: str, is_summary_list: bool = False) -> bool:
    source_rows = _extract_structured_rows(source)
    min_rows = 1 if is_summary_list else 2
    if len(source_rows) < min_rows:
        return True
    normalized_candidate = _normalize_structured_row(candidate)
    return all(row in normalized_candidate for row in source_rows)


class GenerativeFormattingStep(QueryStep):
    """Polish only a single, direct query answer."""

    def __init__(self, llm: Runnable) -> None:
        self.llm = llm
        self.chain: Runnable | None = None
        try:
            self.chain = ChatPromptTemplate.from_messages([("system", PROMPT_TEMPLATE)]) | self.llm | StrOutputParser()
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

        # Lists, grouped summaries, clarification prompts, pagination and
        # coverage responses are composed by the typed presentation planner.
        # Do not make their final response depend on a generative rewrite.
        if query_result.answer_strategy != QueryAnswerStrategy.DIRECT_ANSWER or len(query_result.items or []) > 1:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        # The semantic reasoner already interpreted this turn.  A second LLM
        # must not rewrite the same query decision; deterministic formatting
        # is the authoritative presentation fallback for reasoned answers.
        reasoner_calls = state.get("_query_llm_calls_used")
        if state.get("_query_semantic_llm_used") is True or (isinstance(reasoner_calls, int) and reasoner_calls > 0):
            logger.info(
                "query_direct_answer_formatting_skipped",
                reason="semantic_reasoner_already_used",
                reasoner_calls=reasoner_calls,
            )
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        user_query = state.get("message", "").strip()
        if not user_query:
            return TransactionResult(outcome=TransactionOutcome.OK, patch={})

        locale = LocaleManager.normalize(state.get("language")).value
        current_page = state.get("current_page", 0)
        show_expanded = state.get("show_expanded", False)

        # Generate the deterministic fallback response as the baseline
        system_response = state.get("response")
        if not system_response:
            system_response = QueryFormatter.format(
                query_result,
                current_page=current_page,
                show_expanded=show_expanded,
                has_more=query_result.has_more,
                locale=locale,
            )

        try:
            started_at = perf_counter()
            http_recording_token = start_llm_http_recording()
            try:
                llm_response = await self.chain.ainvoke(
                    {
                        "user_query": user_query,
                        "system_response": system_response,
                        "locale": locale,
                    }
                )
            finally:
                http_metrics = summarize_llm_http_records(stop_llm_http_recording(http_recording_token))
            record_llm_call(
                event_name="query_direct_answer_llm_call",
                duration_ms=(perf_counter() - started_at) * 1000,
                model=None,
                response_type="text",
                system_chars=0,
                user_chars=len(user_query) + len(system_response),
                output_json_chars=len(str(llm_response)),
                extra_fields=http_metrics,
            )
            llm_response = llm_response.strip()
            if not llm_response:
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=system_response,
                    patch={},
                )

            if not _preserves_fact_tokens(source=system_response, candidate=llm_response):
                logger.warning(
                    "generative_formatting_rejected_fact_drift",
                    user_query=user_query,
                    source_tokens=sorted(_extract_fact_tokens(system_response)),
                    candidate_tokens=sorted(_extract_fact_tokens(llm_response)),
                )
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=system_response,
                    patch={},
                )

            if not _preserves_named_entities(source=system_response, candidate=llm_response):
                logger.warning("generative_formatting_rejected_entity_drift")
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=system_response,
                    patch={},
                )

            is_summary_list = query_result.answer_strategy == QueryAnswerStrategy.SUMMARY_LIST
            if not _preserves_structured_rows(
                source=system_response,
                candidate=llm_response,
                is_summary_list=is_summary_list,
            ):
                logger.warning(
                    "generative_formatting_rejected_structure_drift",
                    user_query=user_query,
                    source_rows=_extract_structured_rows(system_response),
                    candidate_rows=_extract_structured_rows(llm_response),
                )
                return TransactionResult(
                    outcome=TransactionOutcome.OK,
                    response=system_response,
                    patch={},
                )

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
