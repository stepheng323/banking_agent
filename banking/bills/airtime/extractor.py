"""LLM-based airtime entity extractor."""

import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from banking.bills.airtime.models.extraction import AirtimeExtractionResult
from banking.bills.airtime.prompt.airtime_extraction import (
    AIRTIME_EXTRACTION_PROMPT,
)
from banking.transactions.shared.models.smart_context import SmartContext
from shared.observability.llm import ainvoke_with_config, build_llm_runnable_config
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_COMPACT_LIST_LIMIT = 4
_FULL_LIST_LIMIT = 6


class AirtimeEntityExtractor:
    """Airtime entity extractor."""

    def __init__(self, llm: BaseChatModel | None = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0, model_kwargs={"seed": 42})
        self.structured = self.llm.with_structured_output(AirtimeExtractionResult)

    @staticmethod
    def _context_mode(smart_context: dict[str, Any] | None) -> str:
        if not smart_context:
            return "minimal"
        if isinstance(smart_context, SmartContext):
            return "compact"
        required_fields = smart_context.get("required_fields", [])
        if isinstance(required_fields, list) and required_fields:
            return "compact"
        if smart_context.get("previousResponse"):
            return "compact"
        if any(smart_context.get(key) for key in ("recipient_phone", "recipient_name", "network")):
            return "compact"
        return "full"

    def _build_context_string(self, smart_context: dict[str, Any] | None) -> str:
        """Build context string from SmartContext or context dict format."""
        if not smart_context:
            return ""

        if isinstance(smart_context, SmartContext):
            return smart_context.to_compact_string()

        parts = []
        context_mode = self._context_mode(smart_context)

        if smart_context.get("previousResponse"):
            parts.append(f"LastMsg: {smart_context['previousResponse'][:150]}")

        required_fields = smart_context.get("required_fields", [])
        if isinstance(required_fields, list) and required_fields:
            parts.append(f"RequiredFields: {', '.join(str(field) for field in required_fields)}")

        if smart_context.get("recipient_phone"):
            parts.append(f"KnownRecipientPhone: {smart_context['recipient_phone']}")
        if smart_context.get("recipient_name"):
            parts.append(f"KnownRecipientName: {smart_context['recipient_name']}")
        if smart_context.get("network"):
            parts.append(f"KnownNetwork: {smart_context['network']}")

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
                limit = _COMPACT_LIST_LIMIT if context_mode == "compact" else _FULL_LIST_LIMIT
                parts.append(f"Beneficiaries: {', '.join(aliases[:limit])}")

        accounts = smart_context.get("accounts", [])
        waiting_for_source_account = isinstance(required_fields, list) and "source_account_id" in required_fields
        if accounts and waiting_for_source_account:
            acc_list = []
            limit = _COMPACT_LIST_LIMIT if context_mode == "compact" else _FULL_LIST_LIMIT
            for idx, acc in enumerate(accounts[:limit], 1):
                name = acc.get("bank_name", "Bank")
                num = acc.get("account_number", "")[-4:]
                acc_list.append(f"{idx}. {name} (...{num})")
            parts.append("Accounts:\n" + "\n".join(acc_list))

        if smart_context.get("language"):
            lang = smart_context["language"]
            parts.append(f"CRITICAL: Reply in {lang.upper()}.")

        return "\n".join(parts)

    async def extract(self, text: str, smart_context: dict[str, Any] | None = None) -> AirtimeExtractionResult:
        """Extract entities from text."""
        user_input = text.strip()
        user_content = user_input

        context_mode = self._context_mode(smart_context)
        context_str = self._build_context_string(smart_context)
        if context_str:
            user_content = f"{user_input}\n\nContext:\n{context_str}"

        start = time.perf_counter()
        result = await ainvoke_with_config(
            self.structured,
            [
                {"role": "system", "content": AIRTIME_EXTRACTION_PROMPT},
                {"role": "user", "content": user_content},
            ],
            config=build_llm_runnable_config(
                role="airtime_extractor",
                phone_number=str(smart_context.get("phone_number") or "") if isinstance(smart_context, dict) else None,
                locale=str(smart_context.get("language") or "") if isinstance(smart_context, dict) else None,
                task_domain="airtime",
                extra_metadata={"context_mode": context_mode},
            )
            or None,
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "airtime_extractor_llm_call",
            duration_ms=round(duration_ms, 2),
            model=getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None),
            system_chars=len(AIRTIME_EXTRACTION_PROMPT),
            user_chars=len(user_content),
            context_chars=len(context_str),
            context_mode=context_mode,
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
