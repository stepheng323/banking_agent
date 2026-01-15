"""Transfer extraction prompt v2 - Pure extraction, no business logic.

CHANGES from v1:
- Removed: missingFields (resolver computes)
- Removed: reply (formatter generates)
- Added: structured ambiguities with candidates
- Added: references for recent transfer detection
- Added: requested_features for unsupported features
- Added: intent_confidence
"""

TRANSFER_EXTRACTION_PROMPT = (
    "Extract transfer entities from user messages. Output ONLY pure JSON.\\n"
    "DO NOT generate reply or decide missing fields - resolver handles that.\\n\\n"
    
    "SCHEMA VERSION: transfer_extract_v2\\n\\n"
    
    "ENTITIES:\\n"
    "- amount: Convert shortcuts (2k→2000, 5h→500). Leave null if unclear or transfer_percentage set\\n"
    "- recipient_account: 10-digit numbers\\n"  
    "- bank_name: Destination bank (STANDARDIZE: 'gtb'→'GTBank', 'zenith'→'Zenith Bank', 'access'→'Access Bank')\\n"
    "- source_bank_name: FROM bank ('from my access', before → or ->)\\n"
    "- recipient_name: Name/alias ('to mum', 'john's gtb')\\n"
    "- narration: Optional memo\\n"
    "- transfer_all: true ONLY for 'move all', 'everything', 'entire balance'\\n"
    "- transfer_percentage: 50 for 'half', 10 for 'tithe', 25 for 'quarter'\\n"
    "- source_accounts: List for dual-account pooling\\n\\n"
    
    "AMBIGUITIES (structured with candidates):\\n"
    "When value is unclear, set field to null and add to ambiguities:\\n"
    '- {"code": "AMOUNT_UNCLEAR", "candidates": [5, 5000]}\\n'
    '- {"code": "MULTIPLE_BENEFICIARIES", "candidates": ["John A", "John B"]}\\n'
    '- {"code": "UNCLEAR_BANK", "candidates": ["First Bank", "First City"]}\\n\\n'
    
    "REFERENCES (for recent transfers):\\n"
    "If user says 'same as before', 'like last time', 'send again':\\n"
    '- references: {"use_recent_transfer": true, "recent_transfer_index": 0}\\n'
    "DO NOT pre-fill entities from recent transfers - resolver handles that.\\n\\n"
    
    "REQUESTED FEATURES (unsupported):\\n"
    "Detect if user wants features beyond simple transfer:\\n"
    "- SCHEDULED: tomorrow, next week, later, Friday, schedule\\n"
    "- RECURRING: every week, monthly, automatic, dey go always\\n"
    "- INTERNATIONAL: abroad, USA, UK, Ghana, overseas\\n\\n"
    
    "CORRECTIONS:\\n"
    "When user corrects a value mid-flow:\\n"
    '- correction: {"field": "AMOUNT", "new_value": 50000}\\n\\n'
    
    "CONFIDENCE:\\n"
    "- intent_confidence: 0.0-1.0 (how confident this is a transfer intent)\\n\\n"
    
    "EXAMPLES:\\n\\n"
    
    'User: "send 5k to mum"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":5000,"recipient_name":"mum"},"correction":null,"ambiguities":[],'
    '"references":{"use_recent_transfer":false},"requested_features":[]}\\n\\n'
    
    'User: "GTB → Access 5k"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.98,'
    '"entities":{"amount":5000,"source_bank_name":"GTBank","bank_name":"Access Bank"},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false},"requested_features":[]}\\n\\n'
    
    'User: "send 5 to john"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.9,'
    '"entities":{"amount":null,"recipient_name":"john"},"correction":null,'
    '"ambiguities":[{"code":"AMOUNT_UNCLEAR","candidates":[5,5000]}],'
    '"references":{"use_recent_transfer":false},"requested_features":[]}\\n\\n'
    
    'User: "send 50k to mum tomorrow"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":50000,"recipient_name":"mum"},"correction":null,"ambiguities":[],'
    '"references":{"use_recent_transfer":false},"requested_features":["SCHEDULED"]}\\n\\n'
    
    'User: "same as last time"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.7,'
    '"entities":{},"correction":null,"ambiguities":[],'
    '"references":{"use_recent_transfer":true,"recent_transfer_index":0},"requested_features":[]}\\n\\n'
    
    'User: "I meant 50k"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":50000},"correction":{"field":"AMOUNT","new_value":50000},"ambiguities":[],'
    '"references":{"use_recent_transfer":false},"requested_features":[]}\\n\\n'
    
    'User: "abeg make am dey go every month"\\n'
    'Output: {"schema_version":"transfer_extract_v2","intent":"transfer","intent_confidence":0.85,'
    '"entities":{},"correction":null,"ambiguities":[],'
    '"references":{"use_recent_transfer":false},"requested_features":["RECURRING"]}\\n'
)


FORMATTER_SYSTEM_PROMPT = """Format banking assistant responses for WhatsApp.
- Keep under 6 short lines
- Prefer bullets over paragraphs
- Format currency as ₦12,345.67
- Be natural, conversational
- For Nigerian users: light Pidgin is okay
"""
