"""Data purchase extraction prompt. Pure extraction, no business logic."""

DATA_EXTRACTION_PROMPT = (
    "Extract data purchase entities from user messages. Output ONLY pure JSON.\\n"
    "DO NOT generate reply or decide missing fields - resolver handles that.\\n"
    "Always output all fields. Use null or empty arrays if not present.\\n\\n"
    "SCHEMA VERSION: data_extract_v2\\n\\n"
    "ENTITIES:\\n"
    "- recipient_phone: Normalize to 11-digit format (08012345678)\\n"
    "- network: MTN, AIRTEL, GLO, 9MOBILE (standardize case)\\n"
    "- budget: Amount user wants to spend (2k→2000, 5h→500). Leave null if unclear\\n"
    "- size_preference: '1GB', '2GB', '5GB', 'weekly', 'monthly', 'daily'\\n"
    "- is_self: true if 'my line', 'for me', 'myself'\\n"
    "- recipient_name: Name/alias if mentioned ('for mum')\\n\\n"
    "AMBIGUITIES (structured with candidates):\\n"
    "When value is unclear, set field to null and add to ambiguities:\\n"
    '- {"code": "BUDGET_UNCLEAR", "candidates": [5, 5000]}\\n'
    '- {"code": "NETWORK_UNCLEAR", "candidates": ["MTN", "Airtel"]}\\n'
    '- {"code": "SIZE_UNCLEAR", "candidates": ["1GB", "2GB"]}\\n\\n'
    "REQUESTED FEATURES (unsupported):\\n"
    "Detect if user wants features beyond simple purchase:\\n"
    "- SCHEDULED: tomorrow, next week, later, Friday\\n"
    "- RECURRING: every week, monthly auto, dey go always\\n\\n"
    "CORRECTIONS (use lowercase field names):\\n"
    "When user corrects a value mid-flow:\\n"
    '- correction: {"field": "budget", "new_value": 5000}\\n\\n'
    "CONFIDENCE:\\n"
    "- intent_confidence: 0.0-1.0 (how confident this is a data purchase intent)\\n\\n"
    "EXAMPLES:\\n\\n"
    'User: "buy 2k MTN data"\\n'
    'Output: {"schema_version":1,"intent":"data","intent_confidence":0.95,'
    '"entities":{"recipient_phone":null,"network":"MTN","budget":2000.0,"size_preference":null,'
    '"is_self":null,"recipient_name":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":[]}\\n\\n'
    'User: "buy 1GB data for my line"\\n'
    'Output: {"schema_version":1,"intent":"data","intent_confidence":0.9,'
    '"entities":{"recipient_phone":null,"network":null,"budget":null,"size_preference":"1GB",'
    '"is_self":true,"recipient_name":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":[]}\\n\\n'
    'User: "get 5GB for 08012345678"\\n'
    'Output: {"schema_version":1,"intent":"data","intent_confidence":0.95,'
    '"entities":{"recipient_phone":"08012345678","network":null,"budget":null,"size_preference":"5GB",'
    '"is_self":null,"recipient_name":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":[]}\\n\\n'
    'User: "buy 5 data"\\n'
    'Output: {"schema_version":1,"intent":"data","intent_confidence":0.8,'
    '"entities":{"recipient_phone":null,"network":null,"budget":null,"size_preference":null,'
    '"is_self":null,"recipient_name":null},'
    '"correction":null,"ambiguities":[{"code":"BUDGET_UNCLEAR","candidates":[5,5000]}],'
    '"references":{"use_recent_purchase":false},"requested_features":[]}\\n\\n'
    'User: "buy 2GB data tomorrow"\\n'
    'Output: {"schema_version":1,"intent":"data","intent_confidence":0.9,'
    '"entities":{"recipient_phone":null,"network":null,"budget":null,"size_preference":"2GB",'
    '"is_self":null,"recipient_name":null},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_purchase":false},'
    '"requested_features":["SCHEDULED"]}\\n\\n'
    'User: "I meant 5k"\\n'
    'Output: {"schema_version":1,"intent":"data","intent_confidence":0.95,'
    '"entities":{"recipient_phone":null,"network":null,"budget":5000.0,"size_preference":null,'
    '"is_self":null,"recipient_name":null},'
    '"correction":{"field":"budget","new_value":5000},"ambiguities":[],'
    '"references":{"use_recent_purchase":false},"requested_features":[]}\\n'
)
