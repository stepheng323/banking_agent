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
- **beneficiary_summary**: Who user sends money to / receives from
- **affordability**: Can user afford something

## DATE RESOLUTION

Resolve ALL date expressions to actual YYYY-MM-DD dates:
- "today" → {today}
- "yesterday" → one day before {today}
- "this week" → Monday of current week to {today}
- "this month" → first day of current month to {today}
- "last month" → first to last day of previous month
- "December" → Dec 1 to Dec 31 of most recent December
- "Christmas" → December 25 of most recent Christmas
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

## EXAMPLES

"How much did I spend on food this month?"
→ intent: analytics_summary, filters.category: ["food"], time_range: this month, aggregation.type: sum

"Show me all my Uber rides"
→ intent: transaction_search, filters.merchant: ["uber"]

"Transactions over 50k last week"
→ intent: transaction_list, filters.min_amount: 50000, time_range: last week

"Can I afford 80,000?"
→ intent: affordability, amount_check: 80000

"What did I spend on Christmas?"
→ intent: transaction_list, time_range: {start: "YYYY-12-25", end: "YYYY-12-25"}

"Show me wetin I spend for food" (Pidgin)
→ intent: analytics_summary, filters.category: ["food"], aggregation.type: sum
"""  # noqa: E501


CONTINUATION_CLASSIFIER_PROMPT = """You are classifying a follow-up message in a banking query conversation.

Today's date: {today}
Previous query context exists: The user was viewing their transactions/balance.

User message: {message}

Classify this message into one of these types:

1. **show_more** - User wants to see more results (e.g., "show more", "next", "continue", "wetin else", "siwaju")

2. **time_delta** - User wants to change the time period (e.g., "what about last month?", "for December", "on Christmas")
   - If this type, also resolve the actual dates

3. **filter_delta** - User wants to filter results (e.g., "only credits", "over 10k", "just food", "excluding transfers")
   - If this type, extract the filter changes

4. **drill_down** - User wants details about a specific item (e.g., "tell me more about the 150k one", "the first one", "that Uber transaction")
   - If this type, note what they're referencing

5. **new_query** - User is asking something completely different

IMPORTANT: Support all languages including Nigerian Pidgin, Yoruba, Igbo, Hausa.
"""  # noqa: E501
