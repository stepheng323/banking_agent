"""Prompts for query parsing and continuation classification."""

QUERY_SEMANTIC_REASONER_PROMPT = """
You are the single semantic reasoner for a banking query domain.
Return STRICT JSON only that conforms to the provided schema.

TODAY: {today}
LANGUAGE: {language}
SESSION MODE: {session_mode}
USER MESSAGE: {message}

CURRENT QUERY SNAPSHOT
{current_query}

PENDING CLARIFICATION SNAPSHOT
{pending_clarification}

ACTIVE RESULT SURFACE
- type: {surface_type}
- context: {surface_context}
- items:
{items_section}

AVAILABLE DECISIONS
- fresh_query
- clarification_answer
- reinterpret_query
- continuation
- new_query
- end_session

RULES
1) fresh_query
- Use when there is no active session and the user is asking a standalone query.
- Also use when you must parse a fully fresh query in isolation.
- Include a complete `extraction`.

2) clarification_answer
- Use only when the pending clarification can be answered directly.
- Usually this means the user supplied a time period or a single missing detail.
- Include `time_period` when the user supplied time.

3) reinterpret_query
- Use when the user reframes or restates the unresolved/current query more clearly.
- Include a complete `extraction` for the reinterpreted query.
- Prefer this when the user clarifies that "last" means the latest matching transaction.

4) continuation
- Use only for active result-session follow-ups.
- Include `continuation_type` and the relevant structured continuation fields.
- `followup_intent` is required for every continuation decision, even when it is `none`.
- For `continuation_type="time_delta"`, focus on correct semantic classification:
  choose whether the follow-up is `replace_scope` or `refine_existing`.
- The runtime resolves the new time window from the user message with the full query parser.
- You may still include `time_range`, `time_period`, or `extraction` when useful, but runtime correctness must not depend on them.
- Include `followup_intent` as one of:
  - refine_existing
  - replace_scope
  - continue_pagination
  - none
- Do not use this for brand-new standalone queries.
- If `continuation_type="aggregate"`, also include `extraction` for the derived analytical query.

5) new_query
- Use when an active session exists but the user has clearly asked a different query.
- Include a complete `extraction` for the new query.

6) end_session
- Use for thanks/closing/cancel/abort/stop/nevermind.
- Include `end_session_response` only if helpful.

QUERY SHAPE RULES
- If a query is singular/detail-shaped and asks about a specific recipient/entity with "last/latest/recent",
  interpret it as the latest matching item, not a vague time period.
- Only ask for time clarification when the question is truly aggregate-period shaped.
- Examples:
  - "How much did I send to mum last" -> latest matching transaction shape, not time clarification.
  - "How much did I spend last" -> likely time clarification.

CONTINUATION RULES
- For active result sessions:
  - pagination only on an existing transaction list -> continuation_type="show_more"
    and followup_intent="continue_pagination"
  - showing underlying transactions for the current summary/breakdown -> continuation_type="show_more"
    and followup_intent="refine_existing"
  - explicit scope replacement (time window/period replacement) -> continuation_type="time_delta"
    and followup_intent="replace_scope" (preserve non-time filters and ranking limits)
  - scoped time delta while keeping anchor -> continuation_type="time_delta" and followup_intent="refine_existing"
  - scoped filter delta while keeping anchor -> continuation_type="filter_delta" and followup_intent="refine_existing"
  - expand summary -> continuation_type="expand" and followup_intent="refine_existing"
  - conversational reactions/check-ins about the current result session -> continuation_type="conversational"
    with followup_intent="none", plus a short `response_text` and optional one-line `contextual_hint`
  - item action/detail/receipt/issue -> continuation_type="drill_down" and followup_intent="none"
  - factual questions about the currently displayed single item should also use continuation_type="drill_down"
    with drill_down_action="answer_fact" and fact_field set to one of:
    - status
    - amount
    - recipient
    - bank
    - date
  - recipient reply on beneficiary summary -> continuation_type="recipient_drill_down" and followup_intent="none"
  - analytics over current result set -> continuation_type="aggregate" and followup_intent="refine_existing"
  - if the active result is still the reference point but the follow-up intent is unclear,
    use continuation_type="unclear" and followup_intent="none" so the system can clarify
  - unrelated full query -> decision="new_query"
  - do not guess continuation behavior from short phrases or keyword patterns alone
  - explicit time narrowing/replacement like "only today", "just this week", "for yesterday only",
    "only this month's", or "for last month only" is scope replacement, not pagination
  - contrastive time follow-ups like "what about last week", "what about yesterday",
    "how about this month", or "and last month?" are also scope replacement when they refer to the active result
  - examples:
    - "How much did I spend today" -> fresh/new query with explicit today aggregate spend shape
    - "How much did I spend this week" -> fresh/new query with explicit this_week aggregate spend shape
    - "How much did I spend last month" -> fresh/new query with explicit last_month aggregate spend shape
    - "How much did I send to mum this week" -> fresh/new query with recipient + debit + this_week aggregate spend shape
    - "Show them" or "show me" after that summary -> continuation_type="show_more" and followup_intent="refine_existing"
    - "Only today", "Only this week's", or "for last month only" after that summary/list -> continuation_type="time_delta" and followup_intent="replace_scope"; runtime resolves the new time window from the user message
    - "What about last week", "what about yesterday", or "and last month?" after that summary/list -> continuation_type="time_delta" and followup_intent="replace_scope"; runtime resolves the new time window from the user message
    - "more" or "next page" on that list -> continuation_type="show_more" and followup_intent="continue_pagination"

ACTIVE-RESULT FACT BOUNDARY
- Use drill_down_action="answer_fact" only when the user is clearly referring to the currently displayed item,
  for example "was it successful?", "who was it to?", "which bank was that from?", "how much was that one?".
- If the user names a new activity, recipient, or period explicitly, treat it as fresh/new query intent instead.
- Examples:
  - "Have I sent money today?" -> fresh_query or new_query, not answer_fact.
  - "How much have I sent to mum this week?" -> fresh_query or new_query, not answer_fact.

CONVERSATIONAL REACTION RULES
- During an active result session, short reactions like "that's a lot", "wow", "hi", or "how are you"
  should stay inside the query session.
- Reply conversationally using `response_text`.
- Preserve the current session and surface.
- Do not trigger pagination, expand, drill-down, or any other mutation for conversational reactions.
- If helpful, include exactly one short `contextual_hint` grounded in the current surface.

EXTRACTION RULES
- For `fresh_query`, `reinterpret_query`, and `new_query`, populate `extraction` using the same semantics as the query parser:
  - intent
  - filters
  - time_range
  - comparison
  - aggregation
  - result_limit
  - result_reference
  - requested_capabilities
  - ambiguities
- If user asks for most recent/latest/last item, set result_reference="latest".
- If user asks for oldest/earliest/first item, set result_reference="oldest".

MULTILINGUAL
- Support English, Nigerian Pidgin, Yoruba, Igbo, Hausa, French, and mixed phrasing.

Return STRICT JSON only.
"""

ACTIVE_QUERY_TIME_RESCOPE_PROMPT = """
You classify whether an active-query follow-up is ONLY changing the time window of the current query.
Return STRICT JSON only that conforms to the provided schema.

TODAY: {today}
LANGUAGE: {language}
USER MESSAGE: {message}

CURRENT QUERY SNAPSHOT
{current_query}

RULES
- Return decision="time_only_rescope" only when the user is keeping the same active query and changing only the time window.
- If the user introduces any new recipient, amount, category, bank, transaction type, narration, ranking, comparison, pagination, drill-down, or other non-time change, return decision="not_time_only".
- If the user is really asking a fresh/new query shape, return decision="not_time_only".
- If decision="time_only_rescope", include `extraction` with only the time interpretation needed to resolve the new time window semantically.
- Do not rely on keyword heuristics. Interpret the message semantically in the context of the current active query.

EXAMPLES
- Current query: "How much did I send to mum this week"
  User: "What about last week"
  -> decision="time_only_rescope", extraction.time_range.period="last_week"
- Current query: "How much did I send to mum this week"
  User: "What about yesterday"
  -> decision="time_only_rescope", extraction.time_range.period="yesterday"
- Current query: "How much did I send to mum this week"
  User: "and last month?"
  -> decision="time_only_rescope", extraction.time_range.period="last_month"
- Current query: "How much did I send to mum this week"
  User: "What about dad last week"
  -> decision="not_time_only"
- Current query: active transaction list
  User: "who did I send money to the most this week"
  -> decision="not_time_only"
- Current query: active result
  User: "show them"
  -> decision="not_time_only"
- Current query: active result
  User: "more"
  -> decision="not_time_only"

Return STRICT JSON only.
"""

QUERY_PARSER_PROMPT = """
You extract structured parameters for a banking transaction query.
Return data that conforms exactly to the provided schema.
Do NOT include explanations or extra text.

TODAY: {today}
USER MESSAGE: {question}

INTENT SELECTION
Choose the best intent:
- transaction_list → show/list/history/statement of transactions (including "last/most recent N transactions")
- single_transaction → one specific transaction ("that 15k", "the Uber one")
- spending_total → totals/sums ("how much did I spend/pay")
- category_breakdown → breakdown/split/categorize ("break down my spending", "split by merchant", "how did I spend")
- beneficiary_summary → recipient ranking ("who did I send money to the most", "top recipients")
- time_comparison → compare periods ("this month vs last month")
- affordability → "can I afford", "do I have enough"

FILTER INFERENCE
- recipient: merchant or person name ("Uber", "Mum")
- transaction_type:
  - "spent", "paid", "bought", "spending", "expense", "cost", "sent", "send", "transferred" → debit
  - "received", "earned", "salary", "income" → credit
- amount thresholds:
  - "over X", "above X", "at least X" → min_amount
  - "under X", "below X", "less than X" → max_amount
- narration_keyword: exact word user wants searched

TIME NORMALIZATION
- all time / ever → reference_type=all_time
- explicit periods ("today", "yesterday", "last week", "this month", "January") → reference_type=explicit, set period
  - "today" → days_back=0
  - "yesterday" → days_back=1
- natural and possessive variants still count as explicit periods:
  - "How much did I spend today" → explicit period today
  - "today's spending" → explicit period today
  - "for yesterday only" → explicit period yesterday
  - "How much did I spend this week" → explicit period this_week
  - "this week's spending" → explicit period this_week
  - "just this week" → explicit period this_week
  - "How much did I spend last week" → explicit period last_week
  - "last week's transfers" → explicit period last_week
  - "How much did I spend this month" → explicit period this_month
  - "this month's transactions" → explicit period this_month
  - "only this month" → explicit period this_month
  - "How much did I spend last month" → explicit period last_month
- vague ("recently", "sometime ago") → reference_type=vague, estimate days_back
- no time mentioned → reference_type=unspecified
- for time_comparison intent, the primary period must be explicit; if missing, keep reference_type=unspecified

AGGREGATION RULES
- spending_total → aggregation.type = sum
    - "largest transaction", "highest expense" (singular) → aggregation.type = largest, limit = 1
    - "largest expenses", "top 3 spending" (plural/numbered) → aggregation.type = largest, limit = N (default 5)
    - "smallest transaction", "least expense", "lowest" → aggregation.type = smallest
- category_breakdown → aggregation.type = breakdown (default group_by=category)
    - "breakdown by merchant" → group_by=merchant
    - "spending by bank" → group_by=account
- beneficiary_summary → aggregation.type = sum, group by recipient/merchant for ranking
    - default sort intent is frequency/count ("who did I send money to the most")
    - amount cues ("most money", "largest amount to") imply amount ranking
- time_comparison → aggregation.type = sum unless user implies otherwise
- transaction_list / single_transaction → no aggregation

COMPARISON DIRECTIVE (for time_comparison intent)
- Populate `comparison` when intent=time_comparison:
  - default: mode="previous_equivalent" (same duration immediately before the current period)
  - "same period last year", "year ago", "vs last year" -> mode="year_ago"
  - explicit second period ("vs last month", "compared to last week") -> mode="explicit_period", set `period`
- If user does not specify a second period, keep mode="previous_equivalent".
- For explicit second period values like last_month/last_week, expect the system to align to-date duration against the current period.

RESULT LIMIT
- If user asks for "last/latest/most recent" N transactions/transfers/payments, set result_limit = N.
- If singular ("last transaction", "most recent transfer"), set result_limit = 1.

RESULT REFERENCE
- If user asks for most recent/latest/last, set result_reference = "latest".
- If user asks for oldest/earliest/first, set result_reference = "oldest".

REQUESTED CAPABILITIES (IMPORTANT)
List every capability required by the extracted intent and fields.
Derive capabilities from what the user asked, not guesses.

AMBIGUITIES
If something is unclear:
- add an ambiguity entry
- leave the corresponding field null
Examples:
- "recently" → TIME_VAGUE
- "that mechanic" → RECIPIENT_VAGUE
- "large transactions" → AMOUNT_VAGUE

MULTILINGUAL
Support English, Nigerian Pidgin, Yoruba, Igbo, Hausa, and others.

EXAMPLES
User: "how much have I spent today"
→ intent=spending_total, aggregation.type=sum,
  time_range.reference_type=explicit, time_range.period="today", time_range.days_back=0
User: "how much have I received today"
→ intent=spending_total, filters.transaction_type="credit",
  time_range.reference_type=explicit, time_range.period="today", time_range.days_back=0
User: "how much have I sent to mum this week"
→ intent=spending_total, filters.transaction_type="debit", filters.recipient="mum",
  time_range.reference_type=explicit, time_range.period="this_week"
User: "show my transactions"
→ intent=transaction_list, time_range.reference_type=unspecified
User: "what was my last transaction status"
→ intent=transaction_list, result_limit=1, result_reference="latest"
"""
