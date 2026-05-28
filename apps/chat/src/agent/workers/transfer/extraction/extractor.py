"""LLM-based transfer entity extractor (multilingual) with enhanced prompt."""

import time
from typing import Any, cast

from langchain_openai import ChatOpenAI

from apps.chat.src.agent.workers.__shared__.models.smart_context import SmartContext
from apps.chat.src.agent.workers.transfer.extraction.prompt import (
    TRANSFER_EXTRACTION_PROMPT,
)
from apps.chat.src.agent.workers.transfer.models.extraction import TransferExtractionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)

_COMPACT_BENEFICIARY_LIMIT = 4
_FULL_BENEFICIARY_LIMIT = 6


class TransferEntityExtractor:
    """Transfer entity extractor."""

    def __init__(self, llm: ChatOpenAI | None = None) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0, model_kwargs={"seed": 42})
        self.structured = self.llm.with_structured_output(TransferExtractionResult)

    @staticmethod
    def _context_mode(smart_context: dict[str, Any] | None) -> str:
        if not smart_context:
            return "minimal"
        if isinstance(smart_context, SmartContext):
            return "compact"
        required_fields = smart_context.get("required_fields", [])
        if isinstance(required_fields, list) and required_fields:
            return "compact"
        if smart_context.get("previousResponse") or smart_context.get("previous_response"):
            return "compact"
        if isinstance(smart_context.get("known_recipient"), dict):
            return "compact"
        return "full"

    @staticmethod
    def _beneficiary_aliases(
        beneficiaries: list[Any],
        *,
        limit: int,
        recipient_hint: str | None = None,
    ) -> list[str]:
        prioritized: list[str] = []
        fallback: list[str] = []
        normalized_hint = (recipient_hint or "").strip().lower()

        for beneficiary in beneficiaries:
            alias: str | None = None
            if isinstance(beneficiary, dict):
                alias = cast(str | None, beneficiary.get("alias") or beneficiary.get("account_name"))
            else:
                alias = cast(str | None, getattr(beneficiary, "alias", None) or getattr(beneficiary, "account_name", None))
            if not alias:
                continue
            bucket = prioritized if normalized_hint and normalized_hint in alias.lower() else fallback
            if alias not in bucket:
                bucket.append(alias)

        aliases = prioritized[:]
        for alias in fallback:
            if alias not in aliases:
                aliases.append(alias)
            if len(aliases) >= limit:
                break
        return aliases[:limit]

    def _build_context_string(self, smart_context: dict[str, Any] | None) -> str:
        """Build context string from SmartContext or context dict format."""
        if not smart_context:
            return ""

        if isinstance(smart_context, SmartContext):
            return cast(str, smart_context.to_compact_string())

        parts = []
        context_mode = self._context_mode(smart_context)

        if smart_context.get("previousResponse"):
            parts.append(f"LastMsg: {smart_context['previousResponse'][:150]}")
        elif smart_context.get("previous_response"):
            parts.append(f"LastMsg: {str(smart_context['previous_response'])[:150]}")

        required_fields = smart_context.get("required_fields", [])
        if isinstance(required_fields, list) and required_fields:
            required = ", ".join(str(field) for field in required_fields)
            parts.append(f"RequiredFields: {required}")

        known_recipient = smart_context.get("known_recipient")
        if isinstance(known_recipient, dict):
            if known_recipient.get("recipient_name"):
                parts.append(f"KnownRecipientName: {known_recipient['recipient_name']}")
            if known_recipient.get("recipient_resolved_name"):
                parts.append(f"KnownResolvedName: {known_recipient['recipient_resolved_name']}")
            if known_recipient.get("recipient_account"):
                parts.append(f"KnownRecipientAccount: {known_recipient['recipient_account']}")
            if known_recipient.get("recipient_bank_name"):
                parts.append(f"KnownRecipientBank: {known_recipient['recipient_bank_name']}")

        active_confirmation_tasks = smart_context.get("active_confirmation_tasks")
        if isinstance(active_confirmation_tasks, list) and active_confirmation_tasks:
            task_lines: list[str] = []
            for item in active_confirmation_tasks[:6]:
                if not isinstance(item, dict):
                    continue
                task_id = str(item.get("task_id") or "").strip()
                recipient = str(item.get("recipient_name") or "").strip()
                resolved = str(item.get("recipient_resolved_name") or "").strip()
                amount = item.get("amount")
                if task_id and (recipient or resolved or isinstance(amount, (int, float))):
                    task_lines.append(
                        f"{task_id}: recipient={recipient or resolved}, resolved={resolved or recipient}, amount={amount}"
                    )
            if task_lines:
                parts.append("ActiveConfirmationTasks:\n" + "\n".join(task_lines))

        beneficiaries = smart_context.get("beneficiaries", [])
        if beneficiaries:
            recipient_hint = None
            if isinstance(known_recipient, dict):
                recipient_hint = cast(str | None, known_recipient.get("recipient_name"))
            limit = _COMPACT_BENEFICIARY_LIMIT if context_mode == "compact" else _FULL_BENEFICIARY_LIMIT
            aliases = self._beneficiary_aliases(beneficiaries, limit=limit, recipient_hint=recipient_hint)
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

        context_mode = self._context_mode(smart_context)
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

        start = time.perf_counter()
        result = await self.structured.ainvoke(
            [
                {"role": "system", "content": TRANSFER_EXTRACTION_PROMPT},
                user_message,
            ]
        )
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "transfer_extractor_llm_call",
            duration_ms=round(duration_ms, 2),
            model=getattr(self.llm, "model_name", None) or getattr(self.llm, "model", None),
            system_chars=len(TRANSFER_EXTRACTION_PROMPT),
            user_chars=len(user_content),
            context_chars=len(context_str),
            context_mode=context_mode,
        )
        if isinstance(result, TransferExtractionResult):
            return result
        return TransferExtractionResult.model_validate(result)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Adapter for pipeline node usage."""
        text = state.get("message", "")
        # Pass the whole state as context so beneficiaries/history can be used
        result = await self.extract(text, smart_context=state)
        return cast(dict[str, Any], result.model_dump())
