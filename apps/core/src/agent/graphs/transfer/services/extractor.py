"""LLM-based transfer entity extractor (multilingual) with enhanced prompt."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.__shared__.models.smart_context import SmartContext
from apps.core.src.agent.graphs.transfer.models.extraction import TransferExtractionResult
from apps.core.src.agent.graphs.transfer.prompt.transfer_extraction import (
    TRANSFER_EXTRACTION_PROMPT,
)


class TransferEntityExtractor:
    """Transfer entity extractor."""

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0, model_kwargs={"seed": 42})
        self.structured = self.llm.with_structured_output(TransferExtractionResult)

    def _build_context_string(self, smart_context: dict[str, Any] | None) -> str:
        """Build context string from SmartContext or legacy dict format."""
        if not smart_context:
            return ""

        if isinstance(smart_context, SmartContext):
            return smart_context.to_compact_string()

        parts = []

        if smart_context.get("previousResponse"):
            parts.append(f"LastMsg: {smart_context['previousResponse'][:150]}")
        beneficiaries = smart_context.get("beneficiaries", [])
        if beneficiaries:
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
                parts.append(f"Beneficiaries: {', '.join(aliases)}")

        if smart_context.get("language"):
            lang = smart_context["language"]
            parts.append(f"CRITICAL: Reply in {lang.upper()}.")

        return "\n".join(parts)

    async def extract(
        self, text: str, smart_context: dict[str, Any] | None = None, image_data: str | None = None
    ) -> TransferExtractionResult:
        """Extract entities from text and optional image."""
        user = text.strip()
        user_content = user

        context_str = self._build_context_string(smart_context)
        if context_str:
            user_content = f"{user}\n\nContext:\n{context_str}"

        if image_data:
            if user_content:
                user_content = (
                    f"{user_content}\n\n"
                    "[An image is attached. Extract any visible bank account number, "
                    "bank name, or other transfer details from the image.]"
                )
            else:
                user_content = (
                    "[An image is attached. Extract any visible bank account number, "
                    "bank name, or other transfer details from the image.]"
                )

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

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Adapter for pipeline node usage."""
        text = state.get("message", "")
        # Pass the whole state as context so beneficiaries/history can be used
        result = await self.extract(text, smart_context=state)
        return result.model_dump()
