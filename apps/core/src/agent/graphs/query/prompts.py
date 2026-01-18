"""Prompts for query parsing and continuation classification."""

QUERY_PARSER_PROMPT = """Parse this financial query into structured parameters.

Today's date: {today}
User's message: {question}

## INTENT TYPES

Choose exactly one:
- **transaction_list**: Show list of transactions (default for browsing)
- **transaction_search**: Find specific transactions by merchant/category
- **analytics_summary**: Aggregate calculations (totals, averages, largest)
- **time_comparison**: Compare periods (vs last month, year-over-year)
- **balance_query**: Current account balance
- **beneficiary_summary**: Who user sends money to / receives from (e.g., "Who do I send money to the most", "Top recipients", "Who pays me")
- **affordability**: Can user afford something

## DATE RESOLUTION

Resolve ALL date expressions to actual YYYY-MM-DD dates:
- "today" → {today}
- "yesterday" → one day before {today}
- "this week" → Monday of current week to {today}
- "this month" → first day of current month to {today}
- "last month" → first to last day of previous month
- "December" → Dec 1 to Dec 31 of the MOST RECENT PAST December (if today is Jan 2026, December = Dec 2025)
- "Christmas" → December 25 of the most recent past Christmas
- "Thanksgiving" → appropriate date
- "last 7 days" → 7 days ago to {today}

## FILTERS

Extract if mentioned:
- **category**: food, transport, entertainment, utilities, airtime, transfers, bank_charges, shopping, savings
- **merchant**: specific company/person names (Uber, Netflix, Mum, etc.)
- **min_amount / max_amount**: amount thresholds ("over 10k" = min_amount: 10000)
- **transaction_type**: "credit" for income, "debit" for expenses
- **exclude**: things to exclude ("excluding bank charges")

## AGGREGATION

For analytics queries:
- **type**: sum, average, count, largest, breakdown
- **group_by**: category, merchant, day, account
- **limit**: number of results (default 10)
- **sort_by**: "amount" (total money) or "count" (frequency)
  - "Who do I send money to the most?" → sort_by: "count" (frequency)
  - "Who have I sent the most money to?" → sort_by: "amount" (total)

## AFFORDABILITY

For affordability queries:
- Extract **amount_check** (in naira)
- Or **item_name** if checking a product
- **analysis_type**: immediate (default), relative, simulated, remainder

## MULTILINGUAL SUPPORT

Parse queries in ANY language including:
- English: "Show my transactions"
- Nigerian Pidgin: "Show me wetin I spend"
- Yoruba: "Fi owo mi han mi"
- Igbo: "Gosi m ego m"
- Hausa: "Nuna mini kudina"

## ACCOUNT SCOPE

Determine which accounts the user is asking about:
- **accounts_scope: "all"** - User wants data from all accounts (default)
  - "across all my accounts", "total balance", "all accounts"
- **accounts_scope: "single"** - User wants data from one specific account
  - If specific: also set **account_name** to the bank/account mentioned
  - Examples: "my GTB account", "from Access Bank", "UBA balance"

Common Nigerian bank names: GTB, Access, Zenith, UBA, First Bank, Kuda, Opay, Moniepoint

## RESULT LIMIT

Extract **result_limit** when user specifies a quantity:
- "last transaction" / "my last transaction" → result_limit: 1
- "last 3 transactions" → result_limit: 3
- "show 5 transactions" → result_limit: 5
- "recent transactions" → result_limit: 5 (default for 'recent')
- If not specified, leave null (default pagination applies)

## EXAMPLES

"How much did I spend on food this month?"
→ intent: analytics_summary, filters.category: ["food"], time_range: this month, aggregation.type: sum

"Show me all my Uber rides"
→ intent: transaction_search, filters.merchant: ["uber"]

"Transactions over 50k last week"
→ intent: transaction_list, filters.min_amount: 50000, time_range: last week

"Show my last transaction"
→ intent: transaction_list, result_limit: 1

"Last 3 transactions"
→ intent: transaction_list, result_limit: 3

"Can I afford 80,000?"
→ intent: affordability, amount_check: 80000

"What's my total balance across all accounts?"
→ intent: balance_query, accounts_scope: "all"

"Show my accounts"
→ intent: balance_query, accounts_scope: "all"

"What's my balance?"
→ intent: balance_query

"Show my GTB transactions"
→ intent: transaction_list, accounts_scope: "single", account_name: "GTB"

"How much did I receive in my Access Bank account?"
→ intent: analytics_summary, accounts_scope: "single", account_name: "Access", filters.transaction_type: "credit", aggregation.type: sum
"""  # noqa: E501

CONTINUATION_CLASSIFIER_PROMPT = """You are classifying a follow-up message in a banking query conversation.

Today's date: {today}
Previous query context exists: The user was viewing their transactions/balance.

User message: {message}
{items_section}
Classify this message into one of these types:

1. **show_more** - User wants to see more results (e.g., "show more", "next", "continue", "wetin else", "siwaju")

2. **time_delta** - User wants to change the time period
(e.g., "what about last month?", "for December", "on Christmas")
   - If this type, also resolve the actual dates

3. **filter_delta** - User wants to filter or search for different results
(e.g., "only credits", "over 10k", "just food", "excluding transfers")
   - Merchant/keyword: "Show only Uber", "Uber related", "just Netflix", "related to food" → set filters.merchant=["keyword"]
   - Transaction type: "only credits", "just debits", "only sent" → set filters.transaction_type
   - Bank/account filter: "just Zenith", "only First Bank" → set filters.account_filter
   - If this type, extract the filter changes (replaces previous filter of same type)

4. **expand** - User wants to see the underlying transactions after a summary
   - e.g., "show transactions", "show me the items", "which ones"
   - Use this ONLY when expanding from an analytics summary (like "You spent ₦X on Y")
   - Do NOT use this when user is already viewing a transaction list

5. **drill_down** - User wants details about a specific transaction OR wants to take action on it
   - Trigger phrases: "show details", "show me details", "tell me more", "what was this?", "details", "more info"
   - If items are provided above, set drill_down_index to the matching item number (0-indexed)
   - If only 1 item exists or user doesn't specify which, default to drill_down_index: 0
   - Match based on: amount ("150k"), ordinal ("first", "second"), description ("Netflix"), or relative ("largest")
   - Set drill_down_action based on intent:
     - **view_details** (default): "tell me more", "what was this?", "show details", "details"
     - **get_receipt**: "send receipt", "proof", "evidence", "I need receipt"
     - **report_issue**: "something's wrong", "I was debited", "failed", "problem", "issue"

6. **recipient_drill_down** - User names a recipient after seeing Top Recipients list
   - e.g., "Uber", "Mum", "Shoprite", "show Uber transactions"
   - Set recipient_name to the matched name (normalize capitalization)
   - Only use this if the context shows a Top Recipients list

7. **unclear** - You cannot understand what the user wants
   - Use this when the message is ambiguous, gibberish, or doesn't match any category
   - IMPORTANT: This preserves context and asks for clarification

8. **end_session** - User is expressing gratitude or ending the conversation
   - e.g., "thank you", "thanks", "arigato", "e se", "na gode", "daalụ", "merci", "gracias", "I'm done", "that's all"
   - Respond with a witty, matching thanks in the same language/style

9. **new_query** - User is asking something completely different
   - IMPORTANT: Use this for queries about accounts/balances even if there's an active session
   - Examples: "show my accounts", "what's my balance", "how much do I have", "list my accounts"
   - Any query that starts fresh should be new_query, NOT drill_down

IMPORTANT: Support all languages including Nigerian Pidgin, Yoruba, Igbo, Hausa, Japanese, etc.
"""
