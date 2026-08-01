"""Surface-specific prompt compiler for query continuation reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from banking.transactions.query.services.reasoning.models import ReasonerPromptProfileType

_VERSION = "v10"

_BASE = """Interpret one multilingual follow-up in an active banking transaction query. Return only schema-valid JSON.
Understand English, Nigerian Pidgin, Yoruba, Hausa, Igbo, and mixed wording.

Use continuation for grounded follow-ups, new_query for a different query shape, reinterpret_query for an explicit
restatement, and end_session only for thanks/cancel/stop. A replacement must include a complete extraction. For a
continuation set continuation_type and followup_intent. Omit unused fields; never guess rows, facts, scopes, or frames.
Runtime code validates selections and computes money, totals, rankings, comparisons, pagination, and account scope.

Meanings: show_more=pagination; show_evidence=rows behind a result; time_delta/filter_delta=scope change;
aggregate/grouped_total_followup=analysis; coverage=completeness/synchronization; recheck=same scope;
drill_down=visible item/fact; reconcile=challenge to this or an earlier answer; unclear=insufficient grounding.
Use repair only for an explicit correction and emit a sparse repair delta when that field exists in the schema.
Use update_preferences only for an explicit persistent instruction or reset and emit only its typed update.
Current wording and grounded scope override preferences. Use a default account preference only when scope is omitted.

Coverage intent: result_completeness for remaining rows/pages, data_coverage for linked-account windows, otherwise
ambiguous. A non-query banking action is never a query continuation.
Extraction intents: history=transaction_list, one fact=transaction_detail, total/breakdown=analytics_summary,
recipient ranking=beneficiary_summary, period comparison=time_comparison, inflow versus outflow=cash_flow_summary.
Preserve explicit filters, time, direction, aggregation, result limit/reference, shape, and fact field.
"""

_FOCUSED = """Focused-item rules:
- Safe referential fact questions use drill_down + answer_fact and fact_field from
status|amount|recipient|counterparty|bank|date|description|reference|account|direction|category.
- Details/receipt/issue/re-transfer use the matching drill_down_action. Respect fact_capabilities in context.
- A summary-scope focus may compile through extraction/filters; never invent a concrete transaction.
- A follow-up that changes only the recipient/counterparty while keeping the current fact, period, direction, and
  other scope (for example, "what about Mum?") is a recipient_drill_down continuation. Return the normalized
  recipient_name and preserve the existing fact_field/result reference; do not treat it as an unclear turn or a
  replacement query.
"""

_REPAIR = """Repair rules:
- The message contains an advisory correction signal, but only classify it as repair when it changes the focused
  query. Return continuation_type=repair, followup_intent=refine_existing, and one sparse repair_delta.
- Omitted delta fields preserve the source contract. Every changed scope uses replace, add, remove, clear, or all.
- If two materially different grounded corrections remain valid, return the best delta and alternate_repair_delta.
- "Keep the same" means preserve: do not emit period, account, category, or dimension mutations for preserved fields.
- Income instead changes measure to income; spending instead changes measure to spending. Do not change account scope,
  period, grouping, or unrelated filters unless the user explicitly changes them.
- A challenge with a correction is reconcile and still includes the sparse repair_delta for the matched source frame.
- Never copy prose into a delta, invent an account/entity, or partially describe a replacement query.
"""

_LIST = """Transaction-list rules:
- Visible references use target_index (1-based), target_amount/text, requested_field, or rank.
- Pagination uses show_more plus next/previous; coverage is only for remaining rows or missing account data.
- Preserve the active period and filters unless explicitly changed.
- Summarize, rank, group, compare, or total the shown scope with continuation_type=aggregate,
  followup_intent=refine_existing, and a complete analytics extraction. Example: spending by account is
  analytics_summary, debit, aggregation.type=breakdown, aggregation.group_by=account.
- A correction from a narrow bucket to whole/all spending drops that bucket, retaining period and direction.
- A challenge to an earlier fact/entity is reconcile, not coverage or item selection.
- A recipient change such as "what about Mum?" is recipient_drill_down with recipient_name set; preserve the
  current fact and scope while changing only the counterparty.
"""

_SUMMARY = """Grouped-summary rules:
- Evidence asks expose underlying transactions; total, ranking, regrouping, and comparison use aggregate.
- Preserve summary scope. A named bucket uses target_text or an extraction filter.
- Highest/lowest keeps the grouping and scope: rank=largest|smallest, aggregate/refine_existing, complete analytics
  extraction, active group_by, aggregation.limit=1. Never treat a bucket as a transaction fact.
- A direction contrast uses transaction_direction_delta=credit|debit|both with filter_delta/refine_existing while
  preserving period and unrelated filters.
- A challenge uses reconcile plus target_text/amount and only supplied frame IDs. Never fabricate a frame.
- A recipient change such as "what about Mum?" is recipient_drill_down with recipient_name set; preserve the
  current fact and summary scope while changing only the counterparty.
- Calculations are deterministic; return only the operation and semantic patch.
"""

_INSIGHT = """Insight rules:
- The active query is an insight contract. Keep decision=continuation for grounded evidence, reconciliation, and
  compatible refinements; never treat an insight aggregate as a generic transaction fact.
- "Show the transactions behind [item]" uses show_evidence/refine_existing. The runtime replays only the typed
  evidence selector from the displayed or retained source frame.
- A challenge across answers uses reconcile with target_text and/or target_amount. Use referenced_frame_ids only for
  frames that appear in the supplied compact frame context; never fabricate an ID.
- If the challenge also states a correction, keep reconcile and include the sparse repair_delta. The runtime applies
  it to the exact matched source-frame contract after explaining the difference.
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

_COMPOSITE = """Composite-result rules:
- The surface contains two or three read-only sections. Ground the follow-up in the named or selected section rather
  than silently applying it to the primary section.
- Set target_step_id only to a step ID present in the supplied composite surface. Never fabricate a step ID.
- A named visible item uses its containing section. An ordinal refers to the flattened visible-item order shown in
  the surface. Use drill_down or show_evidence according to that item's typed payload.
- A scope correction or refinement applies to the targeted step while preserving the other step contracts. Use
  repair with a sparse repair_delta; the runtime performs the immutable contract update.
- If no section is named, preserve semantic focus. Automatic evidence and pagination do not replace that focus.
- A request comparing or combining sections uses aggregate only when the existing typed contracts provide the needed
  values. Otherwise ask a specific clarification; never invent a calculation or hidden result.
- Reconciliation may reference retained frames and must follow the same exact-frame rules as other surfaces.
- A request that adds or replaces a distinct section may emit a complete plan of two or three steps. Retain unchanged
  sections explicitly and keep one primary step. Never return a partial plan draft.
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
        "repair": _REPAIR,
        "focused_item": _FOCUSED,
        "transaction_list": _LIST,
        "grouped_summary": _SUMMARY,
        "insight": _INSIGHT,
        "composite": _COMPOSITE,
        "historical_frames": _FRAMES,
        "pending_clarification": _CLARIFICATION,
    }[profile]
    return CompiledQueryReasonerPrompt(
        system_prompt=f"{_BASE}\n{atom}",
        profile=profile,
        cache_key=f"query-reasoner:{_VERSION}:{profile}",
    )


__all__ = ["CompiledQueryReasonerPrompt", "compile_query_reasoner_prompt"]
