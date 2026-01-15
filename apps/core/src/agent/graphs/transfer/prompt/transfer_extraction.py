"""Transfer extraction prompt. Pure extraction, no business logic."""

TRANSFER_EXTRACTION_PROMPT = (
    "Extract transfer entities from user messages. Output ONLY pure JSON.\\n"
    "DO NOT generate reply or decide missing fields - resolver handles that.\\n"
    "Always output all fields. Use null or empty arrays if not present.\\n\\n"
    
    "SCHEMA VERSION: transfer_extract_v2\\n\\n"
    
    "ENTITIES:\\n"
    "- amount: ALWAYS convert k=×1000 (25k→25000, 5k→5000), h=×100 (5h→500). Extract amount even with account/bank.\\n"
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
    
    "CORRECTIONS (use lowercase field names):\\n"
    "When user corrects a value mid-flow:\\n"
    '- correction: {"field": "amount", "new_value": 50000}\\n\\n'
    
    "CONFIDENCE:\\n"
    "- intent_confidence: 0.0-1.0 (how confident this is a transfer intent)\\n\\n"
    
    "EXAMPLES:\\n\\n"
    
    'User: "send 5k to mum"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":5000,"recipient_name":"mum","recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false,"recent_transfer_index":null},'
    '"requested_features":[]}\\n\\n'
    
    'User: "GTB → Access 5k"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.98,'
    '"entities":{"amount":5000,"source_bank_name":"GTBank","bank_name":"Access Bank","recipient_name":null,'
    '"recipient_account":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false,"recent_transfer_index":null},'
    '"requested_features":[]}\\n\\n'
    
    'User: "Send 25k to 0760505261 Access Bank"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.98,'
    '"entities":{"amount":25000,"recipient_name":null,"recipient_account":"0760505261","bank_name":"Access Bank",'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false,"recent_transfer_index":null},'
    '"requested_features":[]}\\n\\n'
    
    'User: "send 5 to john"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.9,'
    '"entities":{"amount":null,"recipient_name":"john","recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[{"code":"AMOUNT_UNCLEAR","candidates":[5,5000]}],'
    '"references":{"use_recent_transfer":false,"recent_transfer_index":null},"requested_features":[]}\\n\\n'
    
    'User: "send 50k to mum tomorrow"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":50000,"recipient_name":"mum","recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false,"recent_transfer_index":null},'
    '"requested_features":["SCHEDULED"]}\\n\\n'
    
    'User: "send 100k to mum using access and gtb"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":100000,"recipient_name":"mum","recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,'
    '"source_accounts":["Access Bank","GTBank"]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false,"recent_transfer_index":null},'
    '"requested_features":[]}\\n\\n'
    
    'User: "same as last time"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.7,'
    '"entities":{"amount":null,"recipient_name":null,"recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":true,"recent_transfer_index":0},'
    '"requested_features":[]}\\n\\n'
    
    'User: "I meant 50k"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.95,'
    '"entities":{"amount":50000,"recipient_name":null,"recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":{"field":"amount","new_value":50000},"ambiguities":[],'
    '"references":{"use_recent_transfer":false,"recent_transfer_index":null},"requested_features":[]}\\n\\n'
    
    'User: "abeg make am dey go every month"\\n'
    'Output: {"schema_version":1,"intent":"transfer","intent_confidence":0.85,'
    '"entities":{"amount":null,"recipient_name":null,"recipient_account":null,"bank_name":null,'
    '"source_bank_name":null,"narration":null,"transfer_all":null,"transfer_percentage":null,"source_accounts":[]},'
    '"correction":null,"ambiguities":[],"references":{"use_recent_transfer":false,"recent_transfer_index":null},'
    '"requested_features":["RECURRING"]}\\n'
)


FORMATTER_SYSTEM_PROMPT = """Format banking assistant responses for WhatsApp.
- Keep under 6 short lines
- Prefer bullets over paragraphs
- Format currency as ₦12,345.67
- Be natural, conversational
- For Nigerian users: light Pidgin is okay
"""
