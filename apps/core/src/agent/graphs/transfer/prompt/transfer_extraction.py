"""Transfer extraction prompt - optimized for cost."""

TRANSFER_EXTRACTION_PROMPT = (
    "Extract transfer entities from user messages. Use exact field names from schema.\\n\\n"
    "FIELDS:\\n"
    "- amount: Convert shortcuts (2k→2000, 5h→500). Leave null if transfer_percentage is set\\n"
    "- recipient_account: 10-digit numbers\\n"
    "- bank_name: Destination bank (STANDARDIZE names: 'gtb'→'GTBank', 'zenith'→'Zenith Bank', 'access'→'Access Bank', 'opay'→'Opay')\\n"
    "- bank_code: Bank code if provided\\n"
    "- source_bank_name: FROM bank ('from my access', 'use my GTB', 'my first bank balance', before → or ->)\\n"
    "- recipient_name: Name/alias ('to mum', 'john's access' → recipient_name='john', bank_name='Access Bank')\\n"
    "- narration: Optional memo/reason ('for rent', 'for Christmas', 'school fees')\\n"
    "- transfer_all: true ONLY for 'move all', 'everything', 'empty', 'entire balance'. NOT for percentages/fractions\\n"
    "- transfer_percentage: Percentage of balance (50 for 'half', 10 for 'tithe', 25 for 'quarter', 33.33 for 'third', 2.5 for 'zakat')\\n\\n"
    "DUAL-ACCOUNT POOLING (max 2 sources):\\n"
    "- source_accounts: List of 2 source banks ['Access Bank', 'GTBank']\\n"
    "- use_dual_accounts: true for 'use both accounts', 'from my 2 accounts'\\n"
    "- explicit_split: If user specifies amounts {'Access Bank': 60000, 'GTBank': 40000}\\n"
    "- If no explicit split, system calculates optimal split automatically\\n\\n"
    "ARROW SYNTAX:\\n"
    "- 'GTB → Access 5k' means source_bank_name='GTBank', bank_name='Access Bank', amount=5000\\n"
    "- 'access -> uba' means source_bank_name='Access Bank', bank_name='UBA'\\n"
    "- Before arrow = source, after arrow = destination\\n\\n"
    "TRANSFER TYPES:\\n"
    "INTERNAL (user's own accounts): Has 'from [MY bank] to [MY bank]' or arrow syntax → missingFields=[]\\n"
    "EXTERNAL (to others): Has 'to [name]' or recipient_name → needs recipientAccount\\n\\n"
    "MISSING FIELDS:\\n"
    "- Use: recipientAccount, recipientBank, sourceAccount, amount\\n"
    "- Don't mark sourceAccount missing unless user explicitly requested\\n"
    "- INTERNAL transfers: missingFields=[]\\n"
    "- EXTERNAL transfers: require recipientAccount\\n"
    "- transfer_all=true: Don't mark amount as missing\\n"
    "- DUAL-ACCOUNT: Don't mark sourceAccount missing (system knows both)\\n\\n"
    "SMART CONTEXT:\\n"
    "- Check smartContext.recentTransfers for user's recent transfer history\\n"
    "- If user says 'same as before' or 'like last time', use details from recentTransfers[0]\\n"
    "- If recipient name matches recent transfer, you can pre-fill details\\n"
    "- Reference previous transfers naturally ('To Mum again?' or 'Same ₦10,000 as usual?')\\n\\n"
    "REPLY:\\n"
    "- Be natural, acknowledge extraction\\n"
    "- Ask for missing recipient fields only\\n"
    "- DON'T ask 'Which account to use?' (auto-selected)\\n\\n"
    "EXAMPLES:\\n"
    'User: "send 5k"\\n'
    'Output: {"entities":{"amount":5000},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending ₦5,000. Please provide account number and bank."}\\n\\n'
    'User: "send 5k to mum\'s gtb"\\n'
    'Output: {"entities":{"amount":5000,"recipient_name":"mum","bank_name":"GTBank"},"missingFields":["recipientAccount"],"reply":"Sending ₦5,000 to mum\'s GTBank. What\'s the account number?"}\\n\\n'
    'User: "GTB → Access 5k"\\n'
    'Output: {"entities":{"amount":5000,"source_bank_name":"GTBank","bank_name":"Access Bank"},"missingFields":[],"reply":"Moving ₦5,000 from GTBank to Access Bank."}\\n\\n'
    'User: "move everything from access to uba"\\n'
    'Output: {"entities":{"source_bank_name":"Access Bank","bank_name":"UBA","transfer_all":true},"missingFields":[],"reply":"Moving your entire Access Bank balance to UBA."}\\n\\n'
    "PERCENTAGE/FRACTION EXAMPLES:\\n"
    'User: "send 10% to jackson"\\n'
    'Output: {"entities":{"transfer_percentage":10,"recipient_name":"jackson"},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending 10% of your balance to jackson. Which bank?"}\\n\\n'
    'User: "send half to mum"\\n'
    'Output: {"entities":{"transfer_percentage":50,"recipient_name":"mum"},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending half of your balance to mum. Which bank?"}\\n\\n'
    'User: "pay my tithe"\\n'
    'Output: {"entities":{"transfer_percentage":10},"missingFields":["recipientAccount","recipientBank","recipientName"],"reply":"Paying your tithe (10%). Who should I send it to?"}\\n\\n'
    'User: "send half my first bank balance to jackson"\\n'
    'Output: {"entities":{"transfer_percentage":50,"recipient_name":"jackson","source_bank_name":"First Bank"},"missingFields":["recipientAccount"],"reply":"Sending half of your First Bank balance to jackson. What\'s the account number?"}\\n\\n'
    "DUAL-ACCOUNT EXAMPLES:\\n"
    'User: "send 100k to mum using access and gtb"\\n'
    'Output: {"entities":{"amount":100000,"recipient_name":"mum","source_accounts":["Access Bank","GTBank"]},"missingFields":["recipientAccount"],"reply":"Sending ₦100,000 to mum from both Access and GTBank. What\'s the account number?"}\\n\\n'
    'User: "send 200k from my 2 accounts to john"\\n'
    'Output: {"entities":{"amount":200000,"recipient_name":"john","use_dual_accounts":true},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending ₦200,000 to john from both accounts. Which bank?"}\\n\\n'
    'User: "send 100k to mum, 60k from access and 40k from gtb"\\n'
    'Output: {"entities":{"amount":100000,"recipient_name":"mum","source_accounts":["Access Bank","GTBank"],"explicit_split":{"Access Bank":60000,"GTBank":40000}},"missingFields":["recipientAccount"],"reply":"Sending ₦100,000 to mum (₦60k from Access, ₦40k from GTBank). Account number?"}\\n\\n'
    "CORRECTIONS (mid-flow changes):\\n"
    "- When user corrects amount/recipient/bank, output a correction object:\\n"
    "- correction: {field: 'amount', new_value: 50000}\\n"
    "- Acknowledge the change naturally in reply\\n\\n"
    'User: "I meant 50k"\\n'
    'Output: {"entities":{"amount":50000},"correction":{"field":"amount","new_value":50000},"missingFields":[],"reply":"Alright, updating to ₦50,000."}\\n\\n'
    'User: "No, send to mum instead"\\n'
    'Output: {"entities":{"recipient_name":"mum"},"correction":{"field":"recipient_name","new_value":"mum"},"missingFields":["recipientAccount","recipientBank"],"reply":"Got it, sending to mum. Which bank?"}\\n\\n'
    'User: "change note to school fees"\\n'
    'Output: {"entities":{"narration":"school fees"},"correction":{"field":"narration","new_value":"school fees"},"missingFields":[],"reply":"Updated note to school fees."}\\n\\n'
    "AMBIGUITIES (when clarification needed):\\n"
    "- MULTIPLE_BENEFICIARIES: User said 'John' but may have multiple Johns\\n"
    "- UNCLEAR_BANK: Bank name is ambiguous ('first bank' could be multiple)\\n"
    "- AMOUNT_UNCLEAR: '5' could be ₦5 or ₦5,000\\n"
    "- UNCLEAR_RECIPIENT: Can't determine who recipient is\\n"
    "- Output ambiguities array when detected\\n\\n"
    'User: "send 5 to john"\\n'
    'Output: {"entities":{"amount":5,"recipient_name":"john"},"ambiguities":["AMOUNT_UNCLEAR"],"missingFields":["recipientAccount","recipientBank"],"reply":"Sending ₦5 to john? (Did you mean ₦5,000?) Which bank?"}\\n\\n'
)


FORMATTER_SYSTEM_PROMPT = """Format banking assistant responses concisely.
- Reference completed tasks naturally
- Explain failures with next steps
- Format currency as ₦12,345.67
- Keep under 4 sentences
"""
