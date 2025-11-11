"""LLM-based transfer entity extractor (multilingual) with enhanced prompt."""

from typing import Optional, Dict, Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.models.transfer_extraction import TransferExtractionResult


EXTRACTION_SYSTEM_PROMPT = (
    "You are a transfer entity extractor. Extract structured data from user messages about money transfers.\n\n"

    "**TASK:** Extract entities and generate a contextual reply. Use the exact field names from the schema.\n\n"

    "**FIELD EXTRACTION RULES:**\n"
    "1. amount: Convert shortcuts (2k→2000, 5h→500, 10k→10000) to numeric float\n"
    "2. recipient_account: Extract 10-digit numbers (e.g., '0760505261', '0123456789')\n"
    "3. bank_name: Extract bank names (Access bank, GTB, Opay, Zenith, UBA, First Bank) - case insensitive\n"
    "4. bank_code: Extract bank code if provided (alternative to bank_name)\n"
    "5. recipient_name: Extract only if explicitly mentioned (optional)\n"
    "6. narration: Extract transfer description/memo if provided (optional)\n"
    "7. source_account_id: Extract if user specifies which account to use\n\n"

    "**MISSING FIELDS:**\n"
    "- List field names that are REQUIRED but missing (use: 'recipientAccount', 'recipientBank', 'sourceAccount', 'amount')\n"
    "- Exclude optional fields (recipient_name, narration)\n"
    "- If both recipient_account AND bank_name missing, list both\n"
    "- IMPORTANT: Only list 'sourceAccount' as missing if user explicitly asked to change/specify source account\n"
    "- Source account selection happens automatically, so don't mark it as missing unless user explicitly requests it\n"
    "- Empty list means all required fields present\n\n"

    "**REPLY GENERATION:**\n"
    "- Use smartContext.previousResponse for tone/style consistency if provided\n"
    "- Be natural, contextual, and personalized (not robotic)\n"
    "- Acknowledge what you extracted\n"
    "- Ask for missing recipient fields clearly (account number, bank name)\n"
    "- DO NOT ask 'Which account should I use?' - source account is selected automatically\n"
    "- Only ask about source account if user explicitly wants to change/specify it\n\n"

    "**EXAMPLES:**\n"
    'User: "send 5k"\n'
    'Output: {"entities":{"amount":5000},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending ₦5,000. Please provide the account number and bank name."}\n\n'

    'User: "send 5k to doyin"\n'
    'Output: {"entities":{"amount":5000,"recipient_name":"doyin"},"missingFields":["recipientAccount","recipientBank"],"reply":"Sending ₦5,000 to Doyin. Please provide their account number and bank name."}\n\n'

    'User: "0123456789 for lunch"\n'
    'Output: {"entities":{"recipient_account":"0123456789","narration":"for lunch"},"missingFields":["recipientBank"],"reply":"Got it. Which bank is that for?"}\n\n'

    'User: "0760505261 Access bank"\n'
    'Output: {"entities":{"recipient_account":"0760505261","bank_name":"Access bank"},"missingFields":[],"reply":"Got it. Sending to 0760505261 (Access bank)."}\n\n'

    'User: "Opay"\n'
    'Output: {"entities":{"bank_name":"Opay"},"missingFields":["recipientAccount"],"reply":"Got it. Which account number is that for?"}\n\n'

    'User: "send 2k opay 0123456789 birthday"\n'
    'Output: {"entities":{"amount":2000,"bank_name":"opay","recipient_account":"0123456789","narration":"birthday"},"missingFields":[],"reply":"Sending ₦2,000 to Opay - 0123456789 for birthday."}\n'
)


class TransferEntityExtractor:
    """Transfer entity extractor."""

    def __init__(self, llm: Optional[ChatOpenAI] = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)
        self.structured = self.llm.with_structured_output(
            TransferExtractionResult)

    async def extract(self, text: str, smart_context: Optional[Dict[str, Any]] = None) -> TransferExtractionResult:
        """Extract entities from text."""
        user = text.strip()
        user_content = user
        if smart_context:
            user_content = f"{user}\n\nsmartContext: {smart_context}"
        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        if isinstance(result, TransferExtractionResult):
            return result
        return TransferExtractionResult.model_validate(result)
