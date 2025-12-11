"""Transfer extraction prompt - optimized for cost."""

TRANSFER_EXTRACTION_PROMPT = (
    "Extract transfer entities from user messages. Use exact field names from schema.\n\n"
    
    "FIELDS:\n"
    "- amount: Convert shortcuts (2k→2000, 5h→500)\n"
    "- recipient_account: 10-digit numbers\n"
    "- bank_name: Destination bank (uba→UBA, gtb→GTBank, access→Access Bank, opay→Opay)\n"
    "- bank_code: Bank code if provided\n"
    "- source_bank_name: FROM bank only ('from my access', 'use my GTB')\n"
    "- recipient_name: Name/alias ('to mum', 'john's access' → recipient_name='john', bank_name='Access Bank')\n"
    "- narration: Optional memo\n\n"
    
    "TRANSFER TYPES:\n"
    "INTERNAL (user's own accounts): Has 'from [MY bank] to [MY bank]' pattern → missingFields=[]\n"
    "EXTERNAL (to others): Has 'to [name]' or recipient_name → needs recipientAccount\n\n"
    
    "MISSING FIELDS:\n"
    "- Use: recipientAccount, recipientBank, sourceAccount, amount\n"
    "- Don't mark sourceAccount missing unless user explicitly requested\n"
    "- INTERNAL transfers: missingFields=[]\n"
    "- EXTERNAL transfers: require recipientAccount\n\n"
    
    "REPLY:\n"
    "- Be natural, acknowledge extraction\n"
    "- Ask for missing recipient fields only\n"
    "- DON'T ask 'Which account to use?' (auto-selected)\n\n"
    
    "EXAMPLES:\n"
    'User: "send 5k"\n'
    'Output: {"entities":{"amount":5000},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending ₦5,000. Please provide account number and bank."}\n\n'
    
    'User: "send 5k to mum\'s gtb"\n'
    'Output: {"entities":{"amount":5000,"recipient_name":"mum","bank_name":"GTBank"},"missingFields":["recipientAccount"],"reply":"Sending ₦5,000 to mum\'s GTBank. What\'s the account number?"}\n\n'
    
    'User: "0760505261 uba"\n'
    'Output: {"entities":{"recipient_account":"0760505261","bank_name":"UBA"},"missingFields":[],"reply":"Got it. Account 0760505261 (UBA)."}\n\n'
    
    'User: "send 5k from my access to my gtb"\n'
    'Output: {"entities":{"amount":5000,"source_bank_name":"Access Bank","bank_name":"GTBank"},"missingFields":[],"reply":"Transferring ₦5,000 from your Access Bank to GTBank."}\n'
)
