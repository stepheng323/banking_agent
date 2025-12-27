"""LLM-based transfer entity extractor (multilingual) with enhanced prompt."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.sub_agents.transfer.models_extraction import TransferExtractionResult
from apps.core.src.agent.sub_agents.transfer.prompt.transfer_extraction import (
    TRANSFER_EXTRACTION_PROMPT,
)


class TransferEntityExtractor:
    """Transfer entity extractor."""

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0, model_kwargs={"seed": 42})
        self.structured = self.llm.with_structured_output(TransferExtractionResult)

    async def extract(
        self, text: str, smart_context: dict[str, Any] | None = None, image_data: str | None = None
    ) -> TransferExtractionResult:
        """Extract entities from text and optional image."""
        user = text.strip()
        user_content = user

        # Build smart context with beneficiaries info if available
        context_parts = []
        if smart_context:
            if "previousResponse" in smart_context:
                context_parts.append(f"Previous response: {smart_context['previousResponse']}")

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
                    context_parts.append(f"Saved beneficiary aliases/names: {', '.join(aliases)}")

            if "language" in smart_context:
                context_parts.append(
                    f"CRITICAL: User's preferred language is {smart_context['language']}. GENERATE THE REPLY IN {smart_context['language'].upper()}. Adapt the tone to match user's style."
                )

        if context_parts:
            user_content = f"{user}\n\nsmartContext:\n" + "\n".join(context_parts)

        # Build user message - with or without image
        if image_data:
            # Add instruction for image analysis
            if user_content:
                user_content = f"{user_content}\n\n[An image is attached. Please extract any visible bank account number, bank name, or other transfer details from the image.]"
            else:
                user_content = "[An image is attached. Please extract any visible bank account number, bank name, or other transfer details from the image.]"

            user_message = {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": image_data}},
                ],
            }
        else:
            user_message = {"role": "user", "content": user_content}

        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": TRANSFER_EXTRACTION_PROMPT},
                user_message,
            ]
        )
        if isinstance(result, TransferExtractionResult):
            return result
        return TransferExtractionResult.model_validate(result)
