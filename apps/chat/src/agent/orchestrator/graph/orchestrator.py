"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

import re
from typing import Any

from apps.chat.src.agent.orchestrator.config import OrchestratorDependencies
from apps.chat.src.agent.orchestrator.graph.handler import OrchestratorGraphHandler
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from shared.i18n import LocaleManager, render_message
from shared.services.context_manager import OrchestratorContextManager
from shared.services.task_planner import OrchestratorTaskPlanner
from shared.utils.async_helpers import create_background_task

_CAPTION_LABEL_NARRATION_RE = re.compile(
    r"\b(?:narration|memo|note|description|reason|purpose)"
    r"(?:\s+(?:should\s+be|is|as|to\s+be|to|for))?[:\s]+(?P<narration>[^.\n;]+)",
    re.IGNORECASE,
)
_CAPTION_FOR_NARRATION_RE = re.compile(r"\bfor\s+(?P<narration>[^.\n;]+)\s*$", re.IGNORECASE)
_CAPTION_TRANSFER_AMOUNT_RE = re.compile(
    r"\b(?:send|transfer|pay|remit)\b.*?(?:₦|ngn)?\s*"
    r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>[kKhH]?)\b",
    re.IGNORECASE,
)


def _combine_media_text(primary_text: str, media_text: str) -> str:
    """Combine user-authored text and interpreted media text for downstream text-only routing."""
    primary = (primary_text or "").strip()
    media = (media_text or "").strip()
    if primary and media:
        return f"{primary}\n\n{media}"
    return primary or media


def _clean_caption_narration_candidate(value: str) -> str | None:
    candidate = re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,;:")
    if not candidate or len(candidate) > 80:
        return None
    if re.fullmatch(r"(?:₦|ngn)?\s*\d[\d,]*(?:\.\d+)?\s*[kKhH]?", candidate, flags=re.IGNORECASE):
        return None
    if re.fullmatch(r"(?:\d[\s,.\-]?){10,11}", candidate):
        return None
    if re.search(r"\b(?:send|transfer|pay|remit|account|acct)\b", candidate, flags=re.IGNORECASE):
        return None
    return candidate


def _caption_narration_hint(caption: str, image_entities: dict[str, Any] | None = None) -> str | None:
    text = caption.strip()
    if not text:
        return None

    match = _CAPTION_LABEL_NARRATION_RE.search(text) or _CAPTION_FOR_NARRATION_RE.search(text)
    if not match:
        return None

    candidate = _clean_caption_narration_candidate(match.group("narration"))
    if not candidate:
        return None

    normalized_candidate = re.sub(r"[^a-z0-9]+", "", candidate.lower())
    entities = image_entities or {}
    for field in ("recipient_name", "recipient_account", "bank_name"):
        entity_value = entities.get(field)
        if not entity_value:
            continue
        normalized_entity = re.sub(r"[^a-z0-9]+", "", str(entity_value).lower())
        if normalized_entity and (
            normalized_candidate == normalized_entity or normalized_candidate in normalized_entity
        ):
            return None

    return candidate


def _caption_amount_hint(caption: str) -> float | None:
    match = _CAPTION_TRANSFER_AMOUNT_RE.search(caption.strip())
    if not match:
        return None
    try:
        amount = float(match.group("amount").replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        amount *= 1000.0
    elif suffix == "h":
        amount *= 100.0
    return amount if amount > 0 else None


def _format_media_caption_text(caption: str, image_entities: dict[str, Any] | None = None) -> str:
    """Make media captions explicit so downstream text extraction treats them as instructions."""
    text = caption.strip()
    if not text:
        return ""
    lines = [f"User caption/instruction: {text}"]
    amount = _caption_amount_hint(text)
    if amount is not None:
        lines.append(f"Caption-derived transfer fields: amount={amount}.")
    narration = _caption_narration_hint(text, image_entities)
    if narration:
        lines.append(f"Caption-derived transfer fields: narration={narration}.")
    return "\n".join(lines)


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(self, deps: OrchestratorDependencies) -> None:
        self.deps = deps
        self.message_type = "text"

        self.context_manager = OrchestratorContextManager(deps.user_repo, deps.beneficiary_repo, deps.account_repo)
        self.task_planner = OrchestratorTaskPlanner(
            planner_llm=deps.llm,
            semantic_router_llm=deps.semantic_router_llm,
            interrupt_llm=deps.interrupt_llm,
            task_queue_service=deps.task_queue_service,
        )

        self.orchestrator_handler = OrchestratorGraphHandler(
            task_planner=self.task_planner,
            transfer_service=self.deps.transfer_service,
            airtime_service=self.deps.airtime_service,
            query_service=self.deps.query_service,
            data_service=self.deps.data_service,
            account_service=self.deps.account_service,
            support_service=self.deps.support_service,
            faq_service=self.deps.faq_service,
            context_manager=self.context_manager,
            redis_client=self.deps.redis_client,
            user_repo=self.deps.user_repo,
            beneficiary_repo=self.deps.beneficiary_repo,
            account_repo=self.deps.account_repo,
            actionable_message_repo=self.deps.actionable_message_repo,
            banking_provider=self.deps.banking_provider,
            publisher=self.deps.publisher,
            beneficiary_suggestion_service=self.deps.beneficiary_suggestion_service,
            conversation_responder=self.deps.conversation_responder,
        )

    async def resume_transaction(
        self, phone_number: str, flow_type: str, pin_verified: bool, channel: str = "whatsapp"
    ) -> dict[str, Any]:
        """Resume a transaction after an external event (like PIN verification)."""
        payload = {"pin_verified": pin_verified, "flow_type": flow_type}
        return await self.orchestrator_handler.resume_flow(phone_number=phone_number, payload=payload, channel=channel)

    async def invoke(
        self,
        phone_number: str,
        text: str,
        message_id: str,
        message_type: str = "text",
        media_id: str | None = None,
        mime_type: str | None = None,
        quoted_message_id: str | None = None,
        channel: str = "whatsapp",
        channel_identity: str | None = None,
        user: Any | None = None,
    ) -> dict[str, Any]:
        """Invoke the orchestrator with a user message."""
        self.message_type = message_type
        fallback_locale = (await LocaleManager.get_effective_locale(phone_number)).value

        if self.message_type == "audio" and media_id:
            raw_text = await self.deps.media_service.process_audio(media_id, channel=channel, locale=fallback_locale)
            if raw_text:
                audio_fallback = render_message("orchestrator.error.audio_unprocessable", fallback_locale)
                if raw_text == audio_fallback and text.strip():
                    effective_text = text
                elif raw_text == audio_fallback:
                    effective_text = raw_text
                else:
                    effective_text = _combine_media_text(text, f"Transcribed audio: {raw_text}")
                text = effective_text

        if self.message_type == "image" and media_id:
            image_interpretation = await self.deps.media_service.interpret_image(
                media_id,
                channel=channel,
                mime_type=mime_type,
                locale=fallback_locale,
            )
            caption_text = _format_media_caption_text(text, image_interpretation.entities)
            if image_interpretation.text:
                image_text = image_interpretation.text
                if not caption_text:
                    image_text = f"User sent an image with recipient bank details.\n\n{image_text}"
                text = _combine_media_text(caption_text, image_text)
            elif caption_text:
                text = caption_text
            else:
                prompt = render_message("orchestrator.error.image_unprocessable", fallback_locale)
                create_background_task(self.context_manager.add_conversation_turn(phone_number, "user", text))
                create_background_task(self.context_manager.add_conversation_turn(phone_number, "assistant", prompt))
                return {"text": prompt, "intents": [], "outbox": [], "locale": fallback_locale}

        context = MessageContext(
            phone_number=phone_number,
            text=text,
            message_id=message_id,
            image_data=None,
            is_media_input=message_type in {"audio", "image"},
            quoted_message_id=quoted_message_id,
            channel=channel,
            channel_identity=channel_identity,
            resolved_user=user,
        )

        result = await self.orchestrator_handler.invoke(context)
        result_locale = LocaleManager.normalize(result.get("locale") or fallback_locale).value
        result_text = result.get("text")
        has_interactive_output = bool(result.get("intents") or result.get("outbox"))
        final_response = result_text
        if not final_response and not has_interactive_output and not result.get("suppress_empty_fallback"):
            final_response = render_message("orchestrator.fallback.processing_error", result_locale)
            result["text"] = final_response
        result["locale"] = result_locale

        create_background_task(self.context_manager.add_conversation_turn(phone_number, "user", text))
        if final_response:
            create_background_task(
                self.context_manager.add_conversation_turn(phone_number, "assistant", final_response)
            )

        return result
