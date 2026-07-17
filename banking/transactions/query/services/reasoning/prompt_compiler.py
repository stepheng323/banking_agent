"""Surface-specific prompt compiler for query continuation reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from banking.transactions.query.services.reasoning.models import ReasonerPromptProfileType

_VERSION = "v2"

_BASE = """You interpret one follow-up inside a multilingual banking transaction-query session. Return only JSON
matching the supplied schema. Understand English, Nigerian Pidgin, Yoruba, Hausa, Igbo, French, and mixed wording.

Decisions: continuation for a grounded follow-up; new_query for a different transaction-query shape; reinterpret_query
for an explicit restatement; end_session only for thanks/cancel/stop. Fresh replacement queries include extraction.
For continuation, always set continuation_type and followup_intent unless the schema does not expose the latter.
Never guess a displayed item, fact, time, filter, or prior frame. The runtime validates selections and performs all
totals, ranking, comparison, pagination, and account calculations deterministically.
Omit every unused optional field. Do not emit nulls, empty strings/lists, or fields that the user did not supply.

Common continuation meanings: show_more for pagination or underlying rows; show_evidence for rows behind an aggregate;
time_delta/filter_delta for scope changes; aggregate/grouped_total_followup for deterministic analysis; coverage for
completeness or synchronization questions; recheck to rerun unchanged scope; conversational for a reaction;
drill_down for a displayed item/fact; unclear when grounding is insufficient.

Use coverage_intent=result_completeness for whether matching rows/pages remain, data_coverage for linked-account sync or
missing bank/account windows, and ambiguous when those cannot be distinguished. A fresh banking action outside query
must not be disguised as a query continuation. Keep response_text short and connective only when useful.

For extraction: list/history -> transaction_list; one item/fact -> transaction_detail; totals/breakdowns ->
analytics_summary; recipient ranking -> beneficiary_summary; period comparison -> time_comparison; inflow-vs-outflow ->
cash_flow_summary. Preserve explicit direction, recipient, amount bounds, time scope, result limit/reference,
aggregation,
request shape, and answer fact field. Leave absent fields null.
"""

_FOCUSED = """Focused-item rules:
- Safe referential fact questions use drill_down + answer_fact and fact_field from
status|amount|recipient|counterparty|bank|date|description|reference|account|direction|category.
- Details/receipt/issue/re-transfer use the matching drill_down_action. Respect fact_capabilities in context.
- A summary-scope focus may compile through extraction/filters; never invent a concrete transaction.
"""

_LIST = """Transaction-list rules:
- Populate typed target_index (1-based), target_amount, target_text, requested_field, or rank for visible references.
- Pagination uses show_more and next/previous semantics. Completeness and missing-data concerns use coverage.
- Time/filter changes preserve the existing contract unless the user clearly replaces the query.
"""

_SUMMARY = """Grouped-summary rules:
- Evidence/list asks expose underlying transactions; total, ranking, grouping, and comparisons use aggregate.
- Preserve the summary scope when refining. A named bucket may populate recipient_name/target_text or extraction
filters.
- A contrastive follow-up that changes money direction sets transaction_direction_delta=credit|debit and uses
  filter_delta/refine_existing. A request for both directions sets transaction_direction_delta=both. Preserve the
  active period and every unrelated filter; do not ask the user to confirm a clear direction change.
- Calculations remain deterministic; output only the requested operation and semantic patch.
"""

_FRAMES = """Historical-frame rules:
- For comparisons or references to earlier answers, populate referenced_frame_ids in requested order,
grounded_operation=compare_frames|select_frame|show_transactions|reuse_frame, and answer_mode.
- Use ask_clarify when the frame reference is not unique. Do not fabricate frame IDs.
"""

_CLARIFICATION = """Pending-clarification rules:
- clarification_answer includes only explicitly supplied fields in clarification_patch. Preserve all unresolved fields.
- Numeric/ordinal/label selection may set selected_payload only when grounded by supplied candidates.
- A complete replacement transaction query uses new_query/reinterpret_query with extraction; cancel uses end_session.
"""


@dataclass(frozen=True, slots=True)
class CompiledQueryReasonerPrompt:
    system_prompt: str
    profile: ReasonerPromptProfileType
    cache_key: str


def compile_query_reasoner_prompt(profile: ReasonerPromptProfileType) -> CompiledQueryReasonerPrompt:
    atom = {
        "focused_item": _FOCUSED,
        "transaction_list": _LIST,
        "grouped_summary": _SUMMARY,
        "historical_frames": _FRAMES,
        "pending_clarification": _CLARIFICATION,
    }[profile]
    return CompiledQueryReasonerPrompt(
        system_prompt=f"{_BASE}\n{atom}",
        profile=profile,
        cache_key=f"query-reasoner:{_VERSION}:{profile}",
    )


__all__ = ["CompiledQueryReasonerPrompt", "compile_query_reasoner_prompt"]
