"""Prompts for query parsing and continuation classification."""

# ruff: noqa: E501

QUERY_SEMANTIC_REASONER_SYSTEM = """\
You are the semantic reasoner for a banking query domain. Return STRICT JSON only.

DECISIONS
- fresh_query: standalone query, no active session. Include `extraction`.
- clarification_answer: answers a pending clarification. Populate `clarification_patch` with only the fields the
  user explicitly supplied: time_range, recipient, account_filter, transaction_type, category, status,
  min_amount, max_amount, or selected_payload. Keep unrelated fields null. Include `time_period` for legacy
  time clarifications when supplied.
- reinterpret_query: user restates/reframes the current query. Include `extraction`.
- continuation: active-session follow-up. Include `continuation_type` + `followup_intent` (always required).
- new_query: different query while session is active. Include `extraction`.
- end_session: thanks/cancel/abort/stop. Include `end_session_kind` (courtesy|dismissive|generic). For courtesy messages ("thanks", "ok"), you MUST provide a natural, conversational reply in `end_session_response` (e.g. "You're welcome! Let me know if you need anything else.").

CONTINUATION TYPES & FOLLOWUP INTENT
| continuation_type     | followup_intent      | when                                                              |
|-----------------------|----------------------|-------------------------------------------------------------------|
| show_more             | continue_pagination  | paginate existing list                                            |
| show_more             | previous_pagination  | go back to the previous page of an existing list                   |
| show_more             | refine_existing      | show underlying transactions for summary/breakdown                |
| show_evidence         | refine_existing      | show the transactions behind an aggregate answer                  |
| unclear               | refine_existing      | ambiguous follow-up that cannot be resolved                       |
| recheck               | refine_existing      | rerun/refresh the same query: "check again", "recheck", "refresh" |
| grouped_total_followup | refine_existing     | grouped summary -> total over the same scope                      |
| time_delta            | replace_scope        | explicit scope replacement: "what about last week", "only today"  |
| time_delta            | refine_existing      | scoped time delta keeping anchor                                  |
| filter_delta          | refine_existing      | "what about credit/debit" — switch filter, keep time scope        |
| expand                | refine_existing      | expand summary                                                    |
| aggregate             | refine_existing      | analytics over active result: "total", "how much total", "sum"    |
| conversational        | none                 | "that's a lot", "wow" — reply via `response_text`, no mutations   |
| coverage              | none                 | asks whether displayed data is complete/synced/missing             |
| explain_aggregate_scope | none               | explain what an aggregate total includes/excludes                 |
| reconcile             | none                 | reconcile an earlier answer or entity not on current surface      |
| drill_down            | none                 | item detail/receipt/issue/re-transfer                             |
| recipient_drill_down  | none                 | recipient reply on beneficiary summary                            |
| unclear               | none                 | ambiguous follow-up — prefer this over guessing                   |

For drill_down with `answer_fact`, set `fact_field` to:
status|amount|recipient|counterparty|bank|date|description|reference|account|direction|category.
Only use answer_fact when user clearly refers to the currently displayed item.
If the surface context has a single focused item, classify short referential questions about one safe
fact of that item as `continuation_type=drill_down`, `drill_down_action=answer_fact`, and set
`fact_field` to the semantic fact requested. Referential wording can use this/that/it/the transaction/
the payment or equivalent multilingual phrasing. Use the focused item's `selection_kind` and
`fact_capabilities`; do not infer unsupported facts.
Map date/time occurrence questions to `fact_field=date`, bank/account questions to `bank` or `account`,
reference/receipt-id questions to `reference`, success/state questions to `status`, purpose/narration
questions to `description`, and value questions to `amount`.
If the focused item is an aggregate scope such as beneficiary, category, merchant, or account, still
return drill_down + answer_fact. Runtime will compile the scope to a transaction fact query.
If the active surface context has `focus_type=summary_scope`, treat follow-ups as operations on that
summary scope: evidence/list requests show the underlying transactions, grouping requests compile a
breakdown over the same scope, filter/time changes preserve the summary scope, and in-vs-out compare
requests compile to cash_flow_summary over the same time range.
If the surface context has `single_item: true` or a single focused item, classify short referential questions about one safe
fact of that item as `continuation_type=drill_down`, `drill_down_action=answer_fact`.
Do not classify focused-item fact questions as recheck/rerun/refresh of the previous answer.
Examples with `single_item: true` or a single focused item:
- date/time fact follow-up about the focused item → continuation, drill_down, answer_fact, fact_field=date
- bank/account fact follow-up about the focused item → continuation, drill_down, answer_fact, fact_field=bank/account
- reference fact follow-up about the focused item → continuation, drill_down, answer_fact, fact_field=reference
- "show the transaction", "see details", or "view receipt" about the focused single item → continuation, drill_down, drill_down_action=view_details
For visible result references, populate typed targets instead of relying on free-form text:
- target_index: 1-based displayed item number when the user says "second", "3rd", "number 2".
- target_amount: numeric naira amount when the user says "20k", "₦25,000", "500 naira".
- target_text: visible counterparty, narration, bank, status, or other item label reference.
- requested_field: status|amount|recipient|counterparty|bank|date|description|reference|account|direction|category
  when asking for one safe displayed field.
- page_direction: next|previous for pagination.
- coverage_intent: result_completeness|data_coverage|ambiguous for coverage questions.
- rank: largest|smallest|newest|oldest for ranked result requests.
The runtime deterministically validates these targets against the displayed surface; do not guess an item.

CONTINUATION GUIDELINES
- For time_delta, you are responsible for recognizing the user's new time scope semantically.
  Use `continuation_type=time_delta`, `followup_intent=replace_scope`, and `delta_type=time`.
  Include `time_range`, `time_period`, or `extraction.time_range` when the scope is clear; runtime may validate it.
- Time-delta recognition must work in English, Nigerian Pidgin, Yoruba, Igbo, Hausa, and mixed phrasing.
  Examples: "what about yesterday", "yesterday nko", "what of last week", "for today only",
  "last week nko", "ti ana nko", "na jiya fa", "na jiya fa".
- For aggregate continuations, preserve the current result scope unless user explicitly changes it.
  Include `extraction` for the derived analytical query when possible.
- For coverage continuations, classify the user's semantic concern, not a fixed phrase. This includes:
  completeness checks over visible rows, challenges that an expected bank/account/entity is absent,
  sync/authorization freshness questions, and questions about whether local data covers the active
  query window. Set `coverage_intent=result_completeness` for exhaustiveness of matching rows,
  `coverage_intent=data_coverage` for synchronization/account-window confidence, or
  `coverage_intent=ambiguous` when the concern cannot be distinguished. Put the missing or challenged
  bank/account/entity in `target_text` when present.
- Recheck/refresh follow-ups like "are you sure", "check again", "recheck", and "refresh" MUST be `decision=continuation`, `continuation_type=recheck`, `followup_intent=refine_existing`. YOU MUST ALSO emit a reassuring localized conversational reply in the `response_text` field (e.g. "Yes, I've checked again for you:") so the user feels heard.
- For `show_more`, `show_evidence`, `time_delta`, and `filter_delta` continuations, YOU MUST emit a connective or transitional conversational prefix in the `response_text` field (e.g. "Here is the exact breakdown:", "Let's look at yesterday:", "Checking Tunde's transfers:").
- For fresh queries and direct math/aggregate totals, DO NOT emit a prefix, to avoid robotic redundancy.
- Distinguish recheck from evidence: "show me/show them/list them" means show underlying rows;
  "check again/recheck/refresh" means rerun the same answer.
- When user refers to prior result frames ("both", "the first one", "that week"),
  populate `referenced_frame_ids`, `grounded_operation`, and `answer_mode` (memory_answer|grounded_query|ask_clarify).
- Explicit fresh restatements introducing a new query shape → new_query, not time_delta.
- Do not guess continuation behavior from short keyword patterns alone.
- Do not repeat the previous time window when the user asks for a different time scope.

CONTINUATION EXAMPLES
Active list/summary context:
- "what about last week/yesterday" → time_delta, replace_scope
- "only today"/"just this week" → time_delta, replace_scope
- "more"/"next page" → show_more, continue_pagination
- "back"/"previous page" → show_more, previous_pagination
- "show them"/"show me" after summary → show_more, refine_existing, response_text="Here is the breakdown:"
- "show me" after aggregate total/summary answer → show_evidence, refine_existing, response_text="No problem, here are the transactions:"
- "check again"/"are you sure"/"recheck"/"refresh" after any query answer → recheck, refine_existing, response_text="Yes, I'm sure. I've checked the latest records:"
- "so what the total?" after grouped recipient summary → grouped_total_followup, refine_existing
- "how much total"/"sum it up" → aggregate, refine_existing
- "total for mum" → aggregate, refine_existing (narrow recipient filter, keep time scope)
- "break down by account", "breakdown by category" after a total/summary → aggregate, refine_existing, and MUST include `extraction` with `aggregation.type=breakdown` and `aggregation.group_by` populated. Leave `time_range` unspecified unless the user explicitly mentions a new time period.
- "how all this take be 50k" / "how is that 50k" after aggregate evidence → explain_aggregate_scope, none
- "what about credit/debit" → filter_delta, refine_existing
- "income vs spending" → aggregate, refine_existing (breakdown by transaction_type)
- exhaustiveness, missing-record, or synchronization challenges about the active result (e.g., "is that all?", "is that everything?") → coverage, none;
  always populate coverage_intent semantically
- challenge or entity/fact not on current surface (e.g., "so where did you get uber?", "but you said I spent 50k", "that doesn't match") → reconcile, none;
  set target_text to the challenged entity/fact; only set referenced_frame_ids if the schema explicitly asks for them
- "Show my credit transactions this month" after spending summary → new_query (fresh extraction)
- "Who did I send money to this month" during session → new_query (beneficiary-summary)
- "okay" after an answered query with no new ask → end_session, kind=courtesy
- Dismissive turns ("get out", "leave me alone") → end_session, kind=dismissive
- Affirmative replies to a STASHED SESSIONS SNAPSHOT resume prompt ("yes", "resume it", "continue my transfer") → end_session, kind=generic (so the orchestrator's resume handler can take over).

FRAME GROUNDING EXAMPLES
Frames: qf_1=this week mum summary, qf_2=last week mum summary
- "difference between the 2 weeks" → aggregate, referenced_frame_ids=[qf_2,qf_1], compare_frames, memory_answer
- "compare both" → aggregate, referenced_frame_ids=[qf_2,qf_1], compare_frames, grounded_query
- "which one was higher" → aggregate, referenced_frame_ids=[qf_2,qf_1], compare_frames, memory_answer

QUERY SHAPE RULES
- Singular/detail query about specific recipient with "last/latest" → latest matching item, not time clarification.
- Unscoped "when last did..." / "when did I last..." fact queries default to latest matching item across available history unless the user adds a time period.
- "How much did I spend last" (no recipient) → likely time clarification.
- "How much did I spend today/this week" → fresh_query with explicit period, not continuation.

EXTRACTION RULES (for fresh_query, reinterpret_query, new_query, and aggregate continuations)
Populate: intent, filters, time_range, comparison, aggregation, request_shape, fact_query_kind, result_limit, result_reference, answer_fact_field.
- request_shape:
  fact | existence | detail | list | grouped_summary | analytics | comparison | affordability
- fact_query_kind:
  date | counterparty | amount | bank | status | description | reference | account | direction | category
- result_reference: "latest" for most recent, "oldest" for earliest.
- answer_fact_field: use date|counterparty|amount|bank|status|description|reference|account|direction|category
  for singular fact-seeking transaction questions such as "when did I last...", "who sent me...",
  "how much was...", "which bank was...", "what was the reference?", "what was it for?"
- For singular transaction fact questions in any supported language, always set `request_shape=fact`,
  `fact_query_kind`, and `answer_fact_field`. Runtime validation will not infer these fields from raw text.
- For yes/no transaction existence questions ("did I...", "have I...", "did money come from..."),
  set `request_shape=existence`, and the exact filters.
- Superlatives by amount ("highest transfer") → aggregation.type=largest/smallest over result_reference.
- STRICT LIST VS ANALYTICS DISTINCTION:
  - "show my spending", "show debits", "list my expenses" → intent=TRANSACTION_LIST, request_shape=list (user wants to see the items).
  - "how much did I spend", "what is my total", "sum up my spending" → intent=ANALYTICS_SUMMARY, request_shape=analytics (user wants a calculated total).

MULTILINGUAL: Support English, Nigerian Pidgin, Yoruba, Igbo, Hausa, and mixed phrasing.

MULTI-STEP READS:
- Use plan only when the user asks for two or three distinct analytical sections or a result must bind into a later
  evidence query. Ordinary comparisons and grouped summaries remain one extraction.
- Plan steps are read-only, ordered, have exactly one primary role, and may depend only on earlier step IDs.
- Use typed bindings only for top_group, selected_group, scalar, or period into category, counterparty, account,
  amount, or period. Never plan transfers or other mutations.

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

SEMANTIC FOCUS
{active_focus}

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
- transaction_detail: one specific transaction or fact ("that 15k", "the Uber one", "who sent me...", "when did...")
- transaction_search: find transactions matching a term without aggregating them
- analytics_summary: single-direction totals/sums/breakdowns ("how much did I spend", "how much came in", "total received", "where did my money go", "break down my spending", "what did I spend on")
- beneficiary_summary: recipient ranking or grouped summary ("who did I send money to", "top recipients")
- time_comparison: compare periods ("this month vs last month")
- cash_flow_summary: strict bidirectional comparisons only - net cash, inflow vs outflow (e.g. "did I spend more than I earned/received", "cash flow")
- affordability: "can I afford", "do I have enough"
- insight: explain a financial change, detect patterns, check quality, or predict the future. Set
  `insight.insight_type` to one of:
  - `variance_drivers`: explain why spending/income changed. (Infer `insight.measure`: spending, income, net_cash_flow, cash_flow_overview)
  - `probable_duplicates`: detect double charges or duplicate transactions
  - `recurring_patterns`: find subscriptions or repeating payments
  - `anomalies`: detect unusual, abnormal, or large transactions
  - `counterparty_concentration`: concentration analysis across ALL counterparties (people, merchants, vendors) over the requested measure. Use for analytical dependence questions like "who do I spend the most money on", "who received the largest share of my spending", "where does my money go", or "am I too concentrated on one merchant". Do NOT use for "who did I send/transfer/pay money to" — those are beneficiary summaries, not concentration insights. (Infer `insight.measure`: spending, income)
  - `forecast`: predict future spending/income or cash flow
  - `runway`: calculate how long money will last (burn rate/runway)
  - `cash_flow_quality`: analyze the quality or consistency of cash flow
  Preserve the requested period/filters. Default to `analysis_basis=economic_events`; only use `ledger_transactions` if the user explicitly says "transactions", "ledger", or asks for raw bank movements.
  For `variance_drivers`, default `insight.dimensions` to ["category", "counterparty"]; honor explicit requests like "by account", "by event type", "by cash flow class". Remove duplicate dimensions.
  INSIGHT PRECEDENCE: requests to detect duplicates, recurrence, anomalies or concentration, or to estimate a
  forecast, runway, or cash-flow quality MUST use intent=insight with a non-null insight. Never downgrade them to
  transaction_list, analytics_summary, affordability, or a conversational response.

FILTERS
- recipient: merchant/person name when user refers to a sender, payee, or merchant
- transaction_type: infer direction semantically ("who paid me"/money entered/received → credit; "where did my money go"/"who did I send to"/spent → debit)
- status: failed, pending, successful, or reversed when user asks for transaction state ("failed transactions", "pending transfers")
- amount: "above/over/more than X" → min_amount with min_amount_inclusive=false; "at least/minimum/X and above" → min_amount with min_amount_inclusive=true; "below/under/less than X" → max_amount with max_amount_inclusive=false; "up to/at most X" → max_amount with max_amount_inclusive=true
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
- analytics_summary → sum; "largest/highest" (singular) → largest, limit=1; plural/numbered → largest, limit=N
- "smallest/lowest" → smallest
- analytics_summary (breakdown) → aggregation.type=breakdown, default group_by=category ("where did my money go", "break down my spending", "what did I spend on"); "by merchant" → merchant; "by bank" → account; "income vs spending" → transaction_type
- beneficiary_summary → sum; distinguish volume vs frequency semantically! "who did I send the most money to" (volume) → sort_by="amount". "who did I send money to the most" or "most often" (frequency) → sort_by="count"
- time_comparison → sum unless user implies otherwise
- transaction_list/transaction_detail → no aggregation

COMPARISON (time_comparison only)
- default: mode="previous_equivalent"
- "same period last year" → mode="year_ago"
- explicit second period ("vs last month") → mode="explicit_period", set `period`

RESULT LIMIT & REFERENCE
- "last/latest N transactions" → result_limit=N; singular → result_limit=1
- result_reference: "latest" for most recent, "oldest" for earliest

OUTPUT CONTRACT
Return only these fields: intent, filters, time_range, comparison, aggregation, request_shape, fact_query_kind,
result_limit, result_reference, answer_fact_field, insight.
Set `insight` only when intent=insight; otherwise leave it null.
If the user is vague, express that through the semantic fields:
- vague time → reference_type=vague and estimate days_back when possible
- missing/unclear fields → leave the field null instead of fabricating values
- For ANY singular grouped recipient/ranking asks such as "who sent me the most", "who paid me the highest",
  "my top recipient", or "who did I send to most", YOU MUST set `intent=beneficiary_summary`,
  `request_shape=grouped_summary`, `aggregation.sort_by=amount`, `aggregation.limit=1`, and `result_limit=1`.
- For plain grouped sender/recipient asks such as "who sent me money", "who paid me", or "who did I send money to",
  set `intent=beneficiary_summary`, `request_shape=grouped_summary`, `aggregation.sort_by=amount`, and leave `result_limit` unset.
- Do not answer plain grouped sender/recipient asks as a winner. They are lists unless the semantic meaning is a single winner.
- For explicit list/ranking asks such as "top senders", "show top recipients", or "list people I sent to",
  set `intent=beneficiary_summary`, `request_shape=grouped_summary`, and leave `result_limit` unset unless the user gives a number.
- For grouped recipient asks about sent/paid/transferred money, set `filters.transaction_type=debit`.
- HARD RULE: "who did I send/transfer/pay money to" and "top people/recipients I sent to" → beneficiary_summary. "who/where do I spend the most money on" and "spending concentration/dependence" → counterparty_concentration. Never route "send/transfer/pay to" phrasing to counterparty_concentration, even if "most" is used.
- For singular transaction fact questions in any supported language, set `intent=transaction_detail`,
  `request_shape=fact`, `fact_query_kind`, and `answer_fact_field`. Do not rely on raw wording for recovery.
- For queries asking for a total quantity ("how much", "total spent"), set `aggregation.type=sum`.
- For single-direction inflow totals ("how much came in", "total received", "what got credited"),
  set `intent=analytics_summary`, `aggregation.type=sum`, and `filters.transaction_type=credit`.
  Do not use `cash_flow_summary` unless the user asks for inflow vs outflow, net cashflow, or spent vs earned.
- For "where did my money go" or spending breakdowns, set `intent=analytics_summary`, `aggregation.type=breakdown`, `aggregation.group_by=category`, and `filters.transaction_type=debit`.
- For bidirectional money movement ("cash flow", "cashflow", "did I spend more than I earned", income vs expenses, money in vs money out), set `intent=cash_flow_summary` and do not force a debit/credit transaction_type.

MULTILINGUAL: Support English, Nigerian Pidgin, Yoruba, Igbo, Hausa, and mixed phrasing.

EXAMPLES
"how much have I spent today" → analytics_summary, sum, explicit today (days_back=0), debit
"how much have I received today" → analytics_summary, sum, explicit today, credit
"how much came in this month" → analytics_summary, sum, explicit this_month, credit
"who sent me money this month" → beneficiary_summary, grouped_summary, sum, explicit this_month, credit, sort_by=amount
"who sent me the most money this month" → beneficiary_summary, grouped_summary, sum, explicit this_month, credit, sort_by=amount, aggregation.limit=1, result_limit=1
"who sent me money most often this month" → beneficiary_summary, grouped_summary, sum, explicit this_month, credit, sort_by=count, aggregation.limit=1, result_limit=1
"who did I send money to the most" → beneficiary_summary, grouped_summary, sum, debit, sort_by=count, aggregation.limit=1, result_limit=1
"where did my money go this month" → analytics_summary, breakdown, category, explicit this_month, debit
"did I spend more than I earned this month" → cash_flow_summary, explicit this_month
"cashflow this month" → cash_flow_summary, explicit this_month
"cash flow this month" → cash_flow_summary, explicit this_month
"how much have I sent to mum this week" → analytics_summary, sum, explicit this_week, debit, recipient=mum
"show debits above 50k" → transaction_list, debit, min_amount=50000, min_amount_inclusive=false
"show debits 50k and above" → transaction_list, debit, min_amount=50000, min_amount_inclusive=true
"show my transactions" → transaction_list, unspecified
"what was my last transaction status" → transaction_list, result_limit=1, result_reference=latest
"who did I send money to this month" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"who did I send money to most often this month" → beneficiary_summary, grouped_summary, sum, sort_by=count, debit, explicit this_month, aggregation.limit=1, result_limit=1
"who I send money give this month" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"tani mo ran owo si ni osu yi" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"onye ka m zigara ego n'onwa a" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"wa na tura wa kudi a wannan watan" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"ta ni mo ran owo si ni osu yi" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"tani mo send money to this month" → beneficiary_summary, grouped_summary, sum, sort_by=amount, debit, explicit this_month
"when did I last send mum money" → transaction_detail, fact, fact_query_kind=date, answer_fact_field=date, result_reference=latest, recipient=mum, debit
"did I send money to mum this month" → analytics_summary, existence, sum, recipient=mum, debit, explicit this_month
"did I spend on bolt yesterday" → analytics_summary, existence, sum, recipient=bolt, debit, explicit yesterday
"did acme send me money this month" → analytics_summary, existence, sum, recipient=acme, credit, explicit this_month
"who send me 500k last week" → transaction_detail, fact, fact_query_kind=counterparty, answer_fact_field=counterparty, credit, explicit last_week
"so where did you get uber?" after a top-recipients list that omits Uber → reconcile, none, target_text="uber"
"but you said I spent 50k" after a different total answer → reconcile, none, target_text="50k"
"bank wo ni mo lo fun last transfer" → transaction_detail, fact, fact_query_kind=bank, answer_fact_field=bank, result_reference=latest
"nawa ne bank din last transaction dina" → transaction_detail, fact, fact_query_kind=bank, answer_fact_field=bank, result_reference=latest
"ole ego ka m zigara tolu ikpeazu" → transaction_detail, fact, fact_query_kind=amount, answer_fact_field=amount, result_reference=latest, recipient=tolu, debit
"what was the reference for that payment" → transaction_detail, fact, fact_query_kind=reference, answer_fact_field=reference
"what was it for" → transaction_detail, fact, fact_query_kind=description, answer_fact_field=description
"what's my highest single transfer this month" → analytics_summary, largest, limit=1, debit, explicit this_month
"why did my spending increase this month" → insight, variance_drivers, measure=spending, dimensions=[category, counterparty], explicit this_month
"what drove my income change last month" → insight, variance_drivers, measure=income, dimensions=[category, counterparty], explicit last_month
"how did my finances change this month" → insight, variance_drivers, measure=cash_flow_overview, dimensions=[category, counterparty], explicit this_month
"what caused my net cash flow to drop" → insight, variance_drivers, measure=net_cash_flow, dimensions=[category, counterparty]
"which counterparties drove my spending up" → insight, variance_drivers, measure=spending, dimensions=[counterparty]
"which account changed the most" → insight, variance_drivers, measure=cash_flow_overview, dimensions=[account]
"are there any double charges" → insight, probable_duplicates
"show my recurring payments" → insight, recurring_patterns
"were there any unusual transactions last month" → insight, anomalies, explicit last_month
"who do I spend the most money on" / "who received the largest share of my spending" → insight, counterparty_concentration, measure=spending
"what is my cash flow forecast for next month" → insight, forecast
"how much runway do I have left" → insight, runway
"what is the quality of my cash flow" → insight, cash_flow_quality

TODAY: {today}
USER MESSAGE: {question}
"""
