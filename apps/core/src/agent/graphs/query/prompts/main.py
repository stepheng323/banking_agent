"""Prompts for query parsing and continuation classification."""

QUERY_SEMANTIC_REASONER_SYSTEM = """\
You are the semantic reasoner for a banking query domain. Return STRICT JSON only.

DECISIONS
- fresh_query: standalone query, no active session. Include `extraction`.
- clarification_answer: answers a pending clarification (time period, missing detail). Include `time_period` if supplied.
- reinterpret_query: user restates/reframes the current query. Include `extraction`.
- continuation: active-session follow-up. Include `continuation_type` + `followup_intent` (always required).
- new_query: different query while session is active. Include `extraction`.
- end_session: thanks/cancel/abort/stop. Include `end_session_kind` (courtesy|dismissive|generic).

CONTINUATION TYPES & FOLLOWUP INTENT
| continuation_type     | followup_intent      | when                                                              |
|-----------------------|----------------------|-------------------------------------------------------------------|
| show_more             | continue_pagination  | paginate existing list                                            |
| show_more             | refine_existing      | show underlying transactions for summary/breakdown                |
| show_evidence         | refine_existing      | show the transactions behind an aggregate answer                  |
| time_delta            | replace_scope        | explicit scope replacement: "what about last week", "only today"  |
| time_delta            | refine_existing      | scoped time delta keeping anchor                                  |
| filter_delta          | refine_existing      | "what about credit/debit" — switch filter, keep time scope        |
| expand                | refine_existing      | expand summary                                                    |
| aggregate             | refine_existing      | analytics over active result: "total", "how much total", "sum"    |
| conversational        | none                 | "that's a lot", "wow" — reply via `response_text`, no mutations   |
| explain_aggregate_scope | none               | explain what an aggregate total includes/excludes                 |
| drill_down            | none                 | item detail/receipt/issue/re-transfer                             |
| recipient_drill_down  | none                 | recipient reply on beneficiary summary                            |
| unclear               | none                 | ambiguous follow-up — prefer this over guessing                   |

For drill_down with `answer_fact`, set `fact_field` to: status|amount|recipient|bank|date.
Only use answer_fact when user clearly refers to the currently displayed item.

CONTINUATION GUIDELINES
- For time_delta, the runtime resolves the new time window from the user message via the parser.
  You may include `time_range`/`time_period`/`extraction` but runtime must not depend on them.
- For aggregate continuations, preserve the current result scope unless user explicitly changes it.
  Include `extraction` for the derived analytical query when possible.
- When user refers to prior result frames ("both", "the first one", "that week"),
  populate `referenced_frame_ids`, `grounded_operation`, and `answer_mode` (memory_answer|grounded_query|ask_clarify).
- Explicit fresh restatements introducing a new query shape → new_query, not time_delta.
- Do not guess continuation behavior from short keyword patterns alone.

CONTINUATION EXAMPLES
Active list/summary context:
- "what about last week/yesterday" → time_delta, replace_scope
- "only today"/"just this week" → time_delta, replace_scope
- "more"/"next page" → show_more, continue_pagination
- "show them"/"show me" after summary → show_more, refine_existing
- "show me" after aggregate total/summary answer → show_evidence, refine_existing
- "how much total"/"sum it up" → aggregate, refine_existing
- "total for mum" → aggregate, refine_existing (narrow recipient filter, keep time scope)
- "how all this take be 50k" / "how is that 50k" after aggregate evidence → explain_aggregate_scope, none
- "what about credit/debit" → filter_delta, refine_existing
- "income vs spending" → aggregate, refine_existing (breakdown by transaction_type)
- "Show my credit transactions this month" after spending summary → new_query (fresh extraction)
- "Who did I send money to this month" during session → new_query (beneficiary-summary)
- "okay" after an answered query with no new ask → end_session, kind=courtesy
- Dismissive turns ("get out", "leave me alone") → end_session, kind=dismissive

FRAME GROUNDING EXAMPLES
Frames: qf_1=this week mum summary, qf_2=last week mum summary
- "difference between the 2 weeks" → aggregate, referenced_frame_ids=[qf_2,qf_1], compare_frames, memory_answer
- "compare both" → aggregate, referenced_frame_ids=[qf_2,qf_1], compare_frames, grounded_query
- "which one was higher" → aggregate, referenced_frame_ids=[qf_2,qf_1], compare_frames, memory_answer

QUERY SHAPE RULES
- Singular/detail query about specific recipient with "last/latest" → latest matching item, not time clarification.
- "How much did I spend last" (no recipient) → likely time clarification.
- "How much did I spend today/this week" → fresh_query with explicit period, not continuation.

EXTRACTION RULES (for fresh_query, reinterpret_query, new_query)
Populate: intent, filters, time_range, comparison, aggregation, request_shape, fact_query_kind, result_limit, result_reference, answer_fact_field.
- request_shape:
  fact | detail | list | grouped_summary | analytics | comparison | affordability
- fact_query_kind:
  date | counterparty | amount | bank
- result_reference: "latest" for most recent, "oldest" for earliest.
- answer_fact_field: use date|counterparty|amount|bank for singular fact-seeking transaction questions such as
  "when did I last...", "who sent me...", "how much was...", "which bank was..."
- Superlatives by amount ("highest transfer") → aggregation.type=largest/smallest over result_reference.
- query_operation values: list_transactions, search_single_transaction, sum_transactions, count_transactions,
  average_transactions, rank_largest_transaction, rank_smallest_transaction, breakdown_transactions,
  compare_periods, summarize_beneficiaries, check_affordability.

MULTILINGUAL: Support English, Nigerian Pidgin, Yoruba, Igbo, Hausa, French, and mixed phrasing.

Return STRICT JSON only."""

QUERY_SEMANTIC_REASONER_CONTEXT = """\
CURRENT QUERY SNAPSHOT
{current_query}

PENDING CLARIFICATION SNAPSHOT
{pending_clarification}

ACTIVE RESULT SURFACE
- type: {surface_type}
- context: {surface_context}
- items:
{items_section}

RECENT QUERY FRAMES
{query_frames_section}

SESSION METADATA
- mode: {session_mode}
- language: {language}
- today: {today}

USER MESSAGE
{message}"""

QUERY_PARSER_PROMPT = """\
Extract structured parameters for a banking transaction query. Return schema-conformant JSON only.

INTENTS
- transaction_list: show/list/history/statement ("last N transactions")
- single_transaction: one specific transaction ("that 15k", "the Uber one")
- spending_total: totals/sums ("how much did I spend")
- category_breakdown: breakdown/categorize ("break down my spending", "how did I spend")
- beneficiary_summary: recipient ranking or grouped summary ("who did I send money to", "top recipients")
- time_comparison: compare periods ("this month vs last month")
- affordability: "can I afford", "do I have enough"

QUERY OPERATION
The runtime derives `query_operation` from the semantic fields you extract.
Use these semantic targets internally while extracting intent/aggregation:
list_transactions | search_single_transaction | sum_transactions | count_transactions |
average_transactions | rank_largest_transaction | rank_smallest_transaction | breakdown_transactions |
compare_periods | summarize_beneficiaries | check_affordability

FILTERS
- recipient: merchant/person name when user refers to a sender, payee, or merchant
- transaction_type: "spent/paid/sent/transferred" → debit; "received/earned/salary/income" → credit
- amount: "over/above/at least X" → min_amount; "under/below/less than X" → max_amount
- narration_keyword: exact word to search

TIME NORMALIZATION
- all time/ever → reference_type=all_time
- explicit periods → reference_type=explicit, set period:
  today (days_back=0), yesterday (days_back=1), this_week, last_week, this_month, last_month,
  january..december, march_last_year, march_this_year
- Natural/possessive variants count as explicit: "today's spending", "this week's", "just this week", "only this month"
- vague ("recently") → reference_type=vague, estimate days_back
- no time mentioned → reference_type=unspecified
- time_comparison: primary period must be explicit; if missing, keep unspecified

AGGREGATION
- spending_total → sum; "largest/highest" (singular) → largest, limit=1; plural/numbered → largest, limit=N
- "smallest/lowest" → smallest
- category_breakdown → breakdown (default group_by=category; "by merchant" → merchant; "by bank" → account; "income vs spending" → transaction_type)
- beneficiary_summary → sum; sort_by="count" for frequency, sort_by="amount" for amount ranking
- time_comparison → sum unless user implies otherwise
- transaction_list/single_transaction → no aggregation

COMPARISON (time_comparison only)
- default: mode="previous_equivalent"
- "same period last year" → mode="year_ago"
- explicit second period ("vs last month") → mode="explicit_period", set `period`

RESULT LIMIT & REFERENCE
- "last/latest N transactions" → result_limit=N; singular → result_limit=1
- result_reference: "latest" for most recent, "oldest" for earliest

OUTPUT CONTRACT
Return only these fields: intent, filters, time_range, comparison, aggregation, request_shape, fact_query_kind, result_limit, result_reference, answer_fact_field.
If the user is vague, express that through the semantic fields:
- vague time → reference_type=vague and estimate days_back when possible
- missing/unclear fields → leave the field null instead of fabricating values

MULTILINGUAL: Support English, Nigerian Pidgin, Yoruba, Igbo, Hausa, French, and mixed phrasing.

EXAMPLES
"how much have I spent today" → spending_total, sum, explicit today (days_back=0), debit
"how much have I received today" → spending_total, sum, explicit today, credit
"how much have I sent to mum this week" → spending_total, sum, explicit this_week, debit, recipient=mum
"show my transactions" → transaction_list, unspecified
"what was my last transaction status" → transaction_list, result_limit=1, result_reference=latest
"who did I send money to this month" → beneficiary_summary, sum, sort_by=count, debit, explicit this_month
"what's my highest single transfer this month" → spending_total, largest, limit=1, debit, explicit this_month

TODAY: {today}
USER MESSAGE: {question}
"""
