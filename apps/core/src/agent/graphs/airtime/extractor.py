"""LLM-based airtime entity extractor."""

from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.__shared__.models.smart_context import SmartContext
from apps.core.src.agent.graphs.airtime.models import AirtimeExtractionResult
from apps.core.src.agent.graphs.airtime.prompt.airtime_extraction import (
    AIRTIME_EXTRACTION_PROMPT,
)


class AirtimeEntityExtractor:
    """Airtime entity extractor."""

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0, model_kwargs={"seed": 42})
        self.structured = self.llm.with_structured_output(AirtimeExtractionResult)

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

    async def extract(self, text: str, smart_context: dict[str, Any] | None = None) -> AirtimeExtractionResult:
        """Extract entities from text."""
        user_input = text.strip()
        user_content = user_input

        context_str = self._build_context_string(smart_context)
        if context_str:
            user_content = f"{user_input}\n\nContext:\n{context_str}"

        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": AIRTIME_EXTRACTION_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        if isinstance(result, AirtimeExtractionResult):
            return result
        return AirtimeExtractionResult.model_validate(result)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Adapter for pipeline node usage."""
        text = state.get("message", "")
        # Pass the whole state as context so beneficiaries/history can be used
        result = await self.extract(text, smart_context=state)
        return result.model_dump()
