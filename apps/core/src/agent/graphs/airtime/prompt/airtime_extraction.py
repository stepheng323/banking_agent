"""Airtime extraction prompt. Pure extraction, no business logic."""

AIRTIME_EXTRACTION_PROMPT = (
    "Extract airtime purchase entities from user messages. Output ONLY pure JSON.\\n"
    "DO NOT generate reply or decide missing fields - resolver handles that.\\n"
    "Always output all fields. Use null or empty arrays if not present.\\n\\n"
    
    "SCHEMA VERSION: airtime_extract_v2\\n\\n"
    
    "ENTITIES:\\n"
    "- amount: Convert shortcuts (2k→2000.0, 5h→500.0). Leave null if unclear\\n"
    "- recipient_phone: Normalize to 11-digit format (08012345678)\\n"
    "- network: MTN, Airtel, Glo, 9mobile (standardize case)\\n"
    "- recipient_name: Name/alias if mentioned ('for mum')\\n"
    "- is_self: true if 'my line', 'for me', 'myself'\\n"
    "- narration, source_account_id: optional\\n\\n"
    
    "AMBIGUITIES (structured with candidates):\\n"
    "When value is unclear, set field to null and add to ambiguities:\\n"
    '- {"code": "AMOUNT_UNCLEAR", "candidates": [5, 5000]}\\n'
    '- {"code": "NETWORK_UNCLEAR", "candidates": ["MTN", "Airtel"]}\\n\\n'
    
    "REQUESTED FEATURES (unsupported):\\n"
    "Detect if user wants features beyond simple purchase:\\n"
    "- SCHEDULED: tomorrow, next week, later, Friday\\n"
    "- RECURRING: every week, monthly, automatic, dey go always\\n\\n"
    
    "CORRECTIONS (use lowercase field names):\\n"
    "When user corrects a value mid-flow:\\n"
    '- correction: {"field": "amount", "new_value": 5000}\\n\\n'
    
    "CONFIDENCE:\\n"
    "- intent_confidence: 0.0-1.0 (how confident this is an airtime intent)\\n\\n"
    
    "EXAMPLES:\\n\\n"
    
    'User: "buy 2k airtime"\\n'
    'Output: {"schema_version":1,"intent":"airtime","intent_confidence":0.95,'
    '"entities":{"amount":2000.0,"recipient_phone":null,"network":null,"recipient_name":null,'
    '"is_self":null,"narration":null,"source_account_id":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":[]}\\n\\n'
    
    'User: "5k MTN to 08012345678"\\n'
    'Output: {"schema_version":1,"intent":"airtime","intent_confidence":0.98,'
    '"entities":{"amount":5000.0,"recipient_phone":"08012345678","network":"MTN","recipient_name":null,'
    '"is_self":null,"narration":null,"source_account_id":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":[]}\\n\\n'
    
    'User: "buy airtime for my line"\\n'
    'Output: {"schema_version":1,"intent":"airtime","intent_confidence":0.9,'
    '"entities":{"amount":null,"recipient_phone":null,"network":null,"recipient_name":null,'
    '"is_self":true,"narration":null,"source_account_id":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":[]}\\n\\n'
    
    'User: "buy 5 airtime"\\n'
    'Output: {"schema_version":1,"intent":"airtime","intent_confidence":0.85,'
    '"entities":{"amount":null,"recipient_phone":null,"network":null,"recipient_name":null,'
    '"is_self":null,"narration":null,"source_account_id":null},'
    '"correction":null,"ambiguities":[{"code":"AMOUNT_UNCLEAR","candidates":[5,5000]}],'
    '"references":{"use_recent_purchase":false},"requested_features":[]}\\n\\n'
    
    'User: "buy 2k airtime tomorrow"\\n'
    'Output: {"schema_version":1,"intent":"airtime","intent_confidence":0.9,'
    '"entities":{"amount":2000.0,"recipient_phone":null,"network":null,"recipient_name":null,'
    '"is_self":null,"narration":null,"source_account_id":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":["SCHEDULED"]}\\n\\n'
    
    'User: "I meant 5k"\\n'
    'Output: {"schema_version":1,"intent":"airtime","intent_confidence":0.95,'
    '"entities":{"amount":5000.0,"recipient_phone":null,"network":null,"recipient_name":null,'
    '"is_self":null,"narration":null,"source_account_id":null},'
    '"correction":{"field":"amount","new_value":5000},"ambiguities":[],'
    '"references":{"use_recent_purchase":false},"requested_features":[]}\\n'
)
