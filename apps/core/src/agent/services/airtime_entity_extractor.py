"""LLM-based airtime entity extractor."""

from typing import Optional, Dict, Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.models.airtime_extraction import AirtimeExtractionResult


EXTRACTION_SYSTEM_PROMPT = (
    "You are an airtime purchase entity extractor. Extract structured data from user messages about airtime purchases.\n\n"
    "**TASK:** Extract entities and generate a contextual reply.\n\n"
    "**FIELD EXTRACTION RULES:**\n"
    "1. amount: Convert shortcuts (2k→2000, 5h→500, 10k→10000) to numeric float\n"
    "2. recipient_phone: Extract phone numbers (10-11 digits, may include country code)\n"
    "3. network: Extract network name (MTN, Airtel, Glo, 9mobile)\n\n"
    "**MISSING FIELDS:**\n"
    "- List field names that are REQUIRED but missing: 'amount', 'recipientPhone', 'network'\n"
    "- Empty list means all required fields present\n\n"
    "**REPLY GENERATION:**\n"
    "- Be natural and contextual\n"
    "- Acknowledge what you extracted\n"
    "- Ask for missing fields clearly\n"
)


class AirtimeEntityExtractor:
    """Airtime entity extractor."""

    def __init__(self, llm: Optional[ChatOpenAI] = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)
        self.structured = self.llm.with_structured_output(
            AirtimeExtractionResult)

    async def extract(self, text: str, smart_context: Optional[Dict[str, Any]] = None) -> AirtimeExtractionResult:
        """Extract entities from text."""
        # TODO: Implement extraction logic
        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ]
        )
        if isinstance(result, AirtimeExtractionResult):
            return result
        return AirtimeExtractionResult.model_validate(result)

