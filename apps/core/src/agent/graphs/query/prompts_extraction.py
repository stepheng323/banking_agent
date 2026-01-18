"""Query extraction prompt.

Outputs QueryExtractionResult with requested_capabilities for resolver.
"""

QUERY_EXTRACTION_PROMPT = """Extract structured query parameters from this user message.

Today's date: {today}
User's message: {question}

## OUTPUT SCHEMA

You must return a structured object with these fields:

### intent (required)
Choose one:
- **transaction_list**: Show list of transactions
- **spending_total**: Sum/total calculations
- **category_breakdown**: Breakdown by category
- **time_comparison**: Compare periods
- **balance_check**: Account balance
- **single_transaction**: Find specific transaction
- **affordability**: Can I afford X

### filters
- **recipient**: Filter by recipient name (e.g., "Uber", "Mum")
- **min_amount**: Minimum amount in naira
- **max_amount**: Maximum amount in naira
- **category**: food, transport, entertainment, utilities, airtime, etc.
- **transaction_type**: "credit" (income) or "debit" (expense)
- **bank**: Bank/account name
- **narration_keyword**: Exact keyword to search

### time_range
- **reference_type**: 
  - "explicit": Clear date/period (last week, this month, January)
  - "vague": Unclear time (sometime ago, recently)
  - "all_time": All transactions ever
  - "unspecified": No time mentioned
- **period**: Normalized period name (last_week, this_month, january_2026)
- **days_back**: Estimated days if vague

### aggregation (if analytics)
- **type**: sum, count, average, largest
- **group_by**: category, bank, recipient

### requested_capabilities (IMPORTANT)
List ALL capabilities needed for this query:
- **FILTER_RECIPIENT**: Filtering by recipient name
- **FILTER_AMOUNT**: Amount thresholds (over/under X)
- **FILTER_CATEGORY**: Category filtering
- **FILTER_TX_TYPE**: Credit/debit filtering
- **FILTER_BANK**: Bank/account filtering
- **SEARCH_NARRATION_KEYWORD**: Exact keyword search
- **SEARCH_NARRATION_FUZZY**: Fuzzy/semantic search
- **TIME_RELATIVE**: Relative time (last week, this month)
- **TIME_ALL**: All time (all transactions ever)
- **AGGREGATE_SUM**: Sum/total calculations
- **AGGREGATE_GROUP**: Group by category/recipient
- **TIME_COMPARISON**: Compare periods
- **EXPORT_PDF**: Export as PDF
- **EXPORT_CSV**: Export as CSV

### ambiguities
If anything is unclear, add ambiguity entries:
- **TIME_VAGUE**: "sometime ago" - unclear when
- **RECIPIENT_VAGUE**: "that mechanic" - unclear who  
- **AMOUNT_VAGUE**: "large transactions" - unclear threshold

Include "context" (what user said) and optional "suggestion".

## EXAMPLES

"Show me my Uber transactions this month"
-> intent: transaction_list
-> filters.recipient: "Uber"
-> time_range.reference_type: "explicit", period: "this_month"
-> requested_capabilities: ["FILTER_RECIPIENT", "TIME_RELATIVE"]

"How much did I spend on food?"
-> intent: spending_total
-> filters.category: "food"
-> time_range.reference_type: "unspecified"
-> requested_capabilities: ["FILTER_CATEGORY", "AGGREGATE_SUM"]

"Show all my transactions ever"
-> intent: transaction_list
-> time_range.reference_type: "all_time"
-> requested_capabilities: ["TIME_ALL"]

## MULTILINGUAL SUPPORT

Parse queries in any language:
- English: "Show my transactions"
- Pidgin: "Show me wetin I spend"
- Yoruba: "Fi owo mi han mi"
"""
