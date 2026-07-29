"""Surface-specific prompt compiler for query continuation reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from banking.transactions.query.services.reasoning.models import ReasonerPromptProfileType

_VERSION = "v6"

_BASE = """You interpret one follow-up inside a multilingual banking transaction-query session. Return only JSON
matching the supplied schema. Understand English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed wording.

Decisions: continuation for a grounded follow-up; new_query for a different transaction-query shape; reinterpret_query
for an explicit restatement; end_session only for thanks/cancel/stop. Fresh replacement queries include extraction.
For continuation, always set continuation_type and followup_intent unless the schema does not expose the latter.
Never guess a displayed item, fact, time, filter, or prior frame. The runtime validates selections and performs all
totals, ranking, comparison, pagination, and account calculations deterministically.
Omit every unused optional field. Do not emit nulls, empty strings/lists, or fields that the user did not supply.

Common continuation meanings: show_more for pagination or underlying rows; show_evidence for rows behind an aggregate;
time_delta/filter_delta for scope changes; aggregate/grouped_total_followup for deterministic analysis; coverage for
completeness or synchronization questions; recheck to rerun unchanged scope; conversational for a reaction;
drill_down for a displayed item/fact; reconcile when the user challenges an earlier answer or references an entity/fact
that is not on the current surface (e.g., "so where did you get X", "but you said Y", "that doesn't match"); unclear
when grounding is insufficient.

Use repair when the user corrects a prior query interpretation (for example account, person, direction, status,
amount, category, period, measure, or grouping). Emit only a sparse repair_delta. Unmentioned fields are preserved.
If there are two materially different grounded readings, include alternate_repair_delta; never invent candidates or
raw database records. The runtime validates and applies every repair deterministically.

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
- A request to summarize, rank, group, compare, or total the displayed transaction scope is an aggregate refinement,
  not coverage or a clarification. Set decision=continuation, continuation_type=aggregate,
  followup_intent=refine_existing, and extraction with the requested analytics intent/aggregation. Preserve the active
  filters and period unless the user explicitly changes them. For example, spending by account uses
  analytics_summary with aggregation=breakdown/group_by=account and debit filtering.
- Scope-broadening corrections: when the user corrects with "I mean / I meant / no, I meant" plus "whole / all /
  everything / full / total" spending, they want to DROP the active narrow filter (category, merchant, counterparty)
  and keep only the period and direction. Set continuation_type=aggregate, followup_intent=replace_scope,
  delta_type=filter, analytics_summary sum, and NO bucket filter.
- Use coverage only for whether rows are complete, pages remain, account synchronization, or missing-data questions.
- Use reconcile, not coverage, when the user is challenging a fact or entity from an earlier answer rather than asking
  whether all rows or accounts are present.
- A direct correction of the current list's recipient, account, direction, category, status, amount, or period uses
  continuation_type=repair and repair_delta. Do not turn a correction into a fresh parser request.
"""

_SUMMARY = """Grouped-summary rules:
- Evidence/list asks expose underlying transactions; total, ranking, grouping, and comparisons use aggregate.
- Preserve the summary scope when refining. A named bucket may populate recipient_name/target_text or extraction
  filters.
- For an extremum over the current grouped result (for example, which account/category/counterparty was highest or
  lowest), keep the same grouping and scope. Set rank=largest|smallest and return aggregate/refine_existing with a
  complete analytics extraction whose aggregation has the active group_by and limit=1. Never reinterpret a grouped
  bucket as a single transaction, existence query, or transaction fact.
- A contrastive follow-up that changes money direction sets transaction_direction_delta=credit|debit and uses
  filter_delta/refine_existing. A request for both directions sets transaction_direction_delta=both. Preserve the
  active period and every unrelated filter; do not ask the user to confirm a clear direction change.
- RECONCILE: if the user challenges the current answer or names an entity/fact not visible in the current result (e.g.,
  "so where did you get uber?", "but you said I spent 50k", "that doesn't match"), set continuation_type=reconcile,
  populate target_text with the challenged entity/amount/fact, and include referenced_frame_ids only when the schema
  supplies them. Prior query frames are included below; use them to ground the reconciliation. Do not use drill_down or
  coverage for cross-answer challenges.
- Calculations remain deterministic; output only the requested operation and semantic patch.
- Direct corrections to a summary's scope, measure, statistic, or dimension use repair with a sparse repair_delta.
"""

_INSIGHT = """Insight rules:
- The active query is an insight contract. Keep decision=continuation for grounded evidence, reconciliation, and
  compatible refinements; never treat an insight aggregate as a generic transaction fact.
- "Show the transactions behind [item]" uses show_evidence/refine_existing. The runtime replays only the typed
  evidence selector from the displayed or retained source frame.
- A challenge across answers uses reconcile with target_text and/or target_amount. Use referenced_frame_ids only for
  frames that appear in the supplied compact frame context; never fabricate an ID.
- For variance drivers only: income/spending/net-cash-flow changes only the measure; account/category/counterparty
  changes only dimensions; overall financial change uses cash_flow_overview.
- For duplicates, recurring patterns, anomalies, and concentration: retain the active insight type and use evidence
  selection for a named visible item. Do not invent a variance extraction.
- Forecast, runway, and cash-flow quality are summary estimates. Explain their stated period or coverage, but do not
  fabricate transaction evidence when none exists.
- A clearly new transaction query should use new_query. Do not emit a fresh-query extraction here: the runtime will
  safely hand unclear replacements back to normal routing rather than inventing a scope.
- Corrections to period, account, measure, analysis basis, or completeness policy use repair; preserve the insight
  subtype unless the user explicitly replaces it.
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
        "insight": _INSIGHT,
        "historical_frames": _FRAMES,
        "pending_clarification": _CLARIFICATION,
    }[profile]
    return CompiledQueryReasonerPrompt(
        system_prompt=f"{_BASE}\n{atom}",
        profile=profile,
        cache_key=f"query-reasoner:{_VERSION}:{profile}",
    )


__all__ = ["CompiledQueryReasonerPrompt", "compile_query_reasoner_prompt"]
