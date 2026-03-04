"""Prompts for query parsing and continuation classification."""

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
- time_comparison → compare periods ("this month vs last month")
- affordability → "can I afford", "do I have enough"

FILTER INFERENCE
- recipient: merchant or person name ("Uber", "Mum")
- transaction_type:
  - "spent", "paid", "bought", "spending", "expense", "cost" → debit
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
- vague ("recently", "sometime ago") → reference_type=vague, estimate days_back
- no time mentioned → reference_type=unspecified

AGGREGATION RULES
- spending_total → aggregation.type = sum
    - "largest transaction", "highest expense" (singular) → aggregation.type = largest, limit = 1
    - "largest expenses", "top 3 spending" (plural/numbered) → aggregation.type = largest, limit = N (default 5)
    - "smallest transaction", "least expense", "lowest" → aggregation.type = smallest
- category_breakdown → aggregation.type = breakdown (default group_by=category)
    - "breakdown by merchant" → group_by=merchant
    - "spending by bank" → group_by=account
- time_comparison → aggregation.type = sum unless user implies otherwise
- transaction_list / single_transaction → no aggregation

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
User: "show my transactions"
→ intent=transaction_list, time_range.reference_type=unspecified
User: "what was my last transaction status"
→ intent=transaction_list, result_limit=1, result_reference="latest"
"""

CONTINUATION_CLASSIFIER_PROMPT = """
You classify a follow-up message inside an active banking query session.

Today's date: {today}

CONTEXT
- A previous query session exists.
- The user is currently viewing results (or a summary).
- Result Surface: {surface_type}
- Surface Keys/Context: {surface_context}
- Items may be provided below.

USER MESSAGE
{message}

ITEMS (optional)
{items_section}

OUTPUT (STRICT JSON ONLY)
Return exactly one JSON object with:
- continuation_type: one of ["show_more","time_delta","filter_delta","expand","drill_down","recipient_drill_down","unclear","end_session","new_query"]
- confidence: number 0.0-1.0
- reason: short string
- is_new_query_override: boolean
- restates_query: boolean
- delta_type: one of ["filter","time","limit","reference","none"]

Optional fields (fill the ONE relevant to the type):
- time_range: {{ "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "granularity": "day|month" }}
- filters: {{ "merchant": ["string"], "category": ["string"], "transaction_type": "credit|debit", "min_amount": number, "max_amount": number, "account_filter": "string", "exclude": ["string"] }}
- result_limit: number
- result_reference: "latest|oldest"
- drill_down_index: number
- drill_down_action: "view_details|get_receipt|report_issue|re_transfer"
- recipient_name: "string"

CORE RULES (apply in order)
1) END SESSION
If user expresses thanks/closing ("thanks", "I'm done", "e se", etc) => continuation_type="end_session".

2) NEW QUERY OVERRIDE
If user asks something outside the current transaction-viewing session, especially accounts/balance:
Examples: "show my accounts", "what's my balance", "how much do I have"
OR if user issues a fresh command unrelated to the current view:
"Break down my spending", "Show me transfers", "Check airtime"
OR if user restates a full query such as:
"show my last transaction", "show my last 5 transfers", "list my transactions"
=> continuation_type="new_query" AND is_new_query_override=true.
Also set restates_query=true when the message stands alone as a full query.
EXCEPTION: If surface is SUMMARY or BREAKDOWN and user asks to see items
("show transactions", "show my transactions", "list transactions", "show them", "which ones"),
classify as EXPAND, not new_query.

3) SHOW MORE (highest priority among list navigation)
If the message means pagination/continuation ONLY:
"more", "next", "continue", "show more", "next page", "another page", "wetin else", "siwaju", "any others", "others", "any other ones"
=> continuation_type="show_more"
IMPORTANT: Do NOT misclassify these as drill_down.

4) TIME DELTA
If user ONLY changes time period (without restating the full intent):
"last month", "December", "yesterday", "this week", "on Christmas"
=> continuation_type="time_delta" and resolve start/end using {today}.
Set delta_type="time".

5) FILTER DELTA
If user changes filters/search:
- tx type: "only credits", "just debits", "only spent" => transaction_type
- corrections: "I mean debit/credit", "make that debit" => transaction_type
- amount: "over 10k", "below 5k" => min_amount/max_amount
- category: "just food", "only transfers"
- bank: "just Zenith"
- keyword/merchant: "Uber only", "Netflix", "search 'fuel'"
If they specify a count or recency within the same context:
- "last 5", "most recent 3" => set result_limit and result_reference="latest"
If they specify oldest/earliest:
- "oldest 5", "first 3" => set result_limit and result_reference="oldest"
=> continuation_type="filter_delta"
Set delta_type="filter" for filter changes, "limit" for count changes, "reference" for latest/oldest changes.
NOTE: replace the previous filter of the same kind.

6) EXPAND (only from summaries)
Use when current surface is SUMMARY or BREAKDOWN and user asks to see underlying items:
"show transactions", "show my transactions", "list transactions",
"show the items", "which ones", "show them", "list them"
If they also add a time/filter qualifier ("recent", "this month", "over 10k") => continuation_type="new_query".

7) RE-TRANSFER FROM QUERY CONTEXT
If user says "resend", "repeat", "send again", "do it again" while viewing a transaction:
=> continuation_type="drill_down", drill_down_action="re_transfer".

9) DRILL DOWN (details/action on a specific item)
If user asks to see details for a specific item/category:
- "Show details", "tell me more"
- IF SURFACE=BREAKDOWN: "Show [Category]", "What's in [Category]", "Just [Category]" => drill_down (index matching category)
- IF SURFACE=LIST: "Show the first one", "number 5" => drill_down
=> continuation_type="drill_down"
- If items_section exists, select drill_down_index.
CRITICAL: "Break down my spending" IS NOT A DRILL DOWN. It is a NEW QUERY.

10) RECIPIENT DRILL DOWN
Use ONLY if the prior context explicitly shows a "Top Recipients" list and user replies with a name.

11) UNCLEAR
If none fit or message ambiguous/gibberish => continuation_type="unclear".

LANGUAGES
Support English + Nigerian Pidgin + Yoruba/Igbo/Hausa + others.

EXAMPLES
User: "more"
-> {{"continuation_type": "show_more"}}

User: "next page"
-> {{"continuation_type": "show_more"}}

User: "show details"
-> {{"continuation_type": "drill_down", "drill_down_action": "view_details"}}

User: "I need receipt"
-> {{"continuation_type": "drill_down", "drill_down_action": "get_receipt"}}

User: "resend it"
-> {{"continuation_type": "drill_down", "drill_down_action": "re_transfer"}}

User: "show my transactions"
-> {{"continuation_type": "expand"}}

User: "what about last month?"
-> {{"continuation_type": "time_delta"}}

User: "only credits"
-> {{"continuation_type": "filter_delta", "filters": {{"transaction_type": "credit"}}}}

User: "is there any credit?"
-> {{"continuation_type": "filter_delta", "filters": {{"transaction_type": "credit"}}}}

User: "how about debits?"
-> {{"continuation_type": "filter_delta", "filters": {{"transaction_type": "debit"}}}}

User: "what's my balance"
-> {{"continuation_type": "new_query", "is_new_query_override": true}}

Return STRICT JSON only.
"""
