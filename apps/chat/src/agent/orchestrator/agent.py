"""Runtime entrypoint for the banking orchestrator."""

from typing import Any

from apps.chat.src.agent.orchestrator.config.dependencies import OrchestratorDependencies
from apps.chat.src.agent.orchestrator.graph.handler import OrchestratorGraphHandler
from apps.chat.src.agent.orchestrator.models.message_context import MessageContext
from apps.chat.src.agent.orchestrator.services.media_text import combine_media_text, format_media_caption_text
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.services.context_manager import ContextManager
from shared.services.conversation_grounding import conversation_topic_for_response
from shared.services.task_planner import TaskPlanner
from shared.utils.async_helpers import create_background_task


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(self, deps: OrchestratorDependencies) -> None:
        self.deps = deps
        self.message_type = "text"

        self.context_manager = ContextManager(deps.user_repo, deps.beneficiary_repo, deps.account_repo)
        self.task_planner = TaskPlanner(
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
        channel_metadata: dict[str, Any] | None = None,
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
                    effective_text = combine_media_text(text, f"Transcribed audio: {raw_text}")
                text = effective_text

        if self.message_type == "image" and media_id:
            image_interpretation = await self.deps.media_service.interpret_image(
                media_id,
                channel=channel,
                mime_type=mime_type,
                locale=fallback_locale,
            )
            caption_text = format_media_caption_text(text, image_interpretation.entities)
            if image_interpretation.text:
                image_text = image_interpretation.text
                if not caption_text:
                    image_text = f"User sent an image with recipient bank details.\n\n{image_text}"
                text = combine_media_text(caption_text, image_text)
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
            channel_metadata=dict(channel_metadata or {}),
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
            conversation_topic = result.get("conversation_topic") or conversation_topic_for_response(
                final_response,
                semantic_path_shape=result.get("semantic_path_shape"),
            )
            assistant_metadata = {"topic": conversation_topic} if conversation_topic else None
            create_background_task(
                self.context_manager.add_conversation_turn(
                    phone_number,
                    "assistant",
                    final_response,
                    metadata=assistant_metadata,
                )
            )

        return result
