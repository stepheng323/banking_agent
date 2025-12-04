"""LLM-based transfer entity extractor (multilingual) with enhanced prompt."""

from typing import Optional, Dict, Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.models.transfer_extraction import TransferExtractionResult
from apps.core.src.agent.transfer.prompt.transfer_extraction import TRANSFER_EXTRACTION_PROMPT


class TransferEntityExtractor:
    """Transfer entity extractor."""

    def __init__(self, llm: Optional[ChatOpenAI] = None) -> None:
        self.llm = llm or ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            model_kwargs={"seed": 42}
        )
        self.structured = self.llm.with_structured_output(
            TransferExtractionResult)

    async def extract(self, text: str, smart_context: Optional[Dict[str, Any]] = None) -> TransferExtractionResult:
        """Extract entities from text."""
        user = text.strip()
        user_content = user

        # Build smart context with beneficiaries info if available
        context_parts = []
        if smart_context:
            if "previousResponse" in smart_context:
                context_parts.append(
                    f"Previous response: {smart_context['previousResponse']}")

            # Include beneficiaries list to help distinguish aliases from bank names
            if "beneficiaries" in smart_context and smart_context["beneficiaries"]:
                beneficiaries = smart_context["beneficiaries"]
                aliases = []
                for b in beneficiaries:
                    if isinstance(b, dict):
                        alias = b.get("alias") or b.get("account_name")
                        if alias:
                            aliases.append(alias)
                    elif hasattr(b, "alias") and b.alias:
                        aliases.append(b.alias)
                    elif hasattr(b, "account_name") and b.account_name:
                        aliases.append(b.account_name)

                if aliases:
                    context_parts.append(
                        f"Saved beneficiary aliases/names: {', '.join(aliases)}")

        if context_parts:
            user_content = f"{user}\n\nsmartContext:\n" + \
                "\n".join(context_parts)

        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": TRANSFER_EXTRACTION_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        if isinstance(result, TransferExtractionResult):
            return result
        return TransferExtractionResult.model_validate(result)
