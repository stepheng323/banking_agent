"""LLM-based airtime entity extractor."""

from typing import Optional, Dict, Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.sub_agents.airtime.models import AirtimeExtractionResult
from apps.core.src.agent.sub_agents.airtime.prompt.airtime_extraction import AIRTIME_EXTRACTION_PROMPT


class AirtimeEntityExtractor:
    """Airtime entity extractor."""

    def __init__(self, llm: Optional[ChatOpenAI] = None) -> None:
        self.llm = llm or ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            model_kwargs={"seed": 42}
        )
        self.structured = self.llm.with_structured_output(
            AirtimeExtractionResult)

    async def extract(self, text: str, smart_context: Optional[Dict[str, Any]] = None) -> AirtimeExtractionResult:
        """Extract entities from text."""
        user_input = text.strip()
        user_content = user_input

        context_parts = []
        if smart_context:
            if "previousResponse" in smart_context:
                context_parts.append(
                    f"Previous response: {smart_context['previousResponse']}")

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
            user_content = f"{user_input}\n\nsmartContext:\n" + \
                "\n".join(context_parts)

        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": AIRTIME_EXTRACTION_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
        if isinstance(result, AirtimeExtractionResult):
            return result
        return AirtimeExtractionResult.model_validate(result)
