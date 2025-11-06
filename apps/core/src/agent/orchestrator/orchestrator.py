"""Minimal orchestrator: LLM-based multilingual intent+complexity and user context cache."""

from typing import Any, Optional
import json
import asyncio

from langchain_openai import ChatOpenAI

from shared.cache import UserContextCacheService
from shared.clients.whatsapp_client import WhatsAppClient
from shared.repositories.user_repository import UserRepository
from shared.utils.serialization import sqlalchemy_to_dict
from shared.repositories.beneficiary_repository import BeneficiaryRepository
from shared.repositories.account_repository import AccountRepository
from shared.database.connection import get_db_session
from shared.cache.redis_client import RedisClient

from apps.core.src.agent.models.classification import ClassificationResult
from apps.core.src.agent.services.conversation_responder import ConversationResponder
from apps.core.src.agent.transfer import TransferService


class OrchestratorAgent:
    """Orchestrator agent for the banking assistant."""

    def __init__(
        self,
        llm: ChatOpenAI | None = None,
        user_repo: Optional[UserRepository] = None,
        user_cache: Optional[UserContextCacheService] = None,
    ) -> None:
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0)
        self.classifier_llm = self.llm.with_structured_output(
            ClassificationResult)
        self.user_repo = user_repo
        self.user_cache = user_cache or UserContextCacheService()
        self.whatsapp_client = WhatsAppClient()
        self.conversation = ConversationResponder(self.llm)
        beneficiary_repo = BeneficiaryRepository(get_db_session())
        account_repo = AccountRepository(get_db_session())
        self.transfer = TransferService(
            llm=self.llm,
            user_cache=self.user_cache,
            beneficiary_repo=beneficiary_repo,
            account_repo=account_repo,
            whatsapp_client=self.whatsapp_client,
        )

    async def _load_user_context(self, phone_number: str) -> dict[str, Any]:
        cached = await self.user_cache.get(phone_number)
        if cached:
            return cached

        profile = None
        accounts: list[dict[str, Any]] = []
        if self.user_repo:
            profile = self.user_repo.get_by_phone(phone_number)

        safe_profile: dict[str, Any] | None = sqlalchemy_to_dict(
            profile) if profile is not None else None

        context = {
            "profile": safe_profile,
            "accounts": accounts,
        }
        await self.user_cache.set(phone_number, context)
        return context

    async def _get_conversation_state(self, phone_number: str) -> Optional[dict[str, Any]]:
        """Get the current conversation/flow state for this user."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:conversation_state"
            data = await redis_client.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    async def _get_last_response(self, phone_number: str) -> Optional[str]:
        """Get last assistant response from Redis (fast, for LLM context)."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_response"
            return await redis_client.get(key)
        except Exception:
            pass
        return None

    async def _save_last_response(self, phone_number: str, response: str) -> None:
        """Save last assistant response to Redis (non-blocking, for LLM context)."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_response"
            # Save with 1 hour TTL
            await redis_client.set(key, response, ex=3600)
        except Exception:
            pass  # Don't fail if cache update fails

    async def _classify_llm(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
        last_response: Optional[str] = None,
    ) -> ClassificationResult:
        system = (
            "You are an intent classifier for a banking assistant. "
            "Classify messages into: transfer, airtime, data, conversational, unknown. "
            "Determine complexity (multi-step reasoning, dynamic amounts, pooling accounts, historical references, multiple transactions). "
            "Work across languages: English, Yoruba, Hausa, Igbo, Nigerian Pidgin, French, and more.\n\n"
            "**CONTEXT AWARENESS (HIGHEST PRIORITY):**\n"
            "- If context.conversationState exists with active_flow='transfer', classify as 'transfer' (continuation)\n"
            "- If previous assistant response asked for transfer details (account, bank, amount), and user provides them, classify as 'transfer'\n"
            "- Account numbers (10 digits), bank names, or combinations ('0760505261 Access bank') are transfer continuations\n"
            "- Short responses to transfer questions are continuations\n\n"
            "**EXAMPLES:**\n"
            "- '0760505261 Access bank' → transfer (providing requested info)\n"
            "- 'Access bank' → transfer (answering bank question)\n"
            "- '5k' → transfer (providing amount after being asked)\n"
            "- 'send 5k' → transfer (new request)\n"
            "- 'hi' → conversational\n"
            "- 'check balance' → conversational\n\n"
            "**PRINCIPLE:** If the message answers a question or provides requested information, it's a continuation. Otherwise, classify based on intent.\n"
            "Return ONLY the JSON for the given schema."
        )

        user_content = text.strip()

        if last_response:
            user_content = f"{user_content}\n\n[Previous assistant response: {last_response}]"

        if context:
            if context.get("conversationState"):
                conv_state = context["conversationState"]
                user_content = (
                    f"{user_content}\n\n"
                    f"[Context: Active flow: {conv_state.get('active_flow')}, "
                    f"Flow state: {conv_state.get('flow_state')}]\n"
                    f"This message is likely providing information for the ongoing {conv_state.get('active_flow')} flow."
                )

        raw = await self.classifier_llm.ainvoke(
            [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]
        )
        if isinstance(raw, ClassificationResult):
            return raw
        return ClassificationResult.model_validate(raw)

    async def invoke(self, phone_number: str, text: str, _message_id: str) -> str:
        """Invoke the orchestrator agent."""
        if not text or not text.strip():
            return "Please send a message with your request."

        _user_ctx = await self._load_user_context(phone_number)

        conversation_state = await self._get_conversation_state(phone_number)
        last_response = await self._get_last_response(phone_number)

        classification_context = {}
        if conversation_state:
            classification_context["conversationState"] = conversation_state

        result = await self._classify_llm(
            text,
            classification_context if classification_context else None,
            last_response,
        )

        print(f"Classification result: {result.model_dump_json()}")

        intent = result.intent.lower()
        complex_note = "complex" if result.is_complex else "simple"

        response = (
            f"Intent: {intent}\n"
            f"Complexity: {complex_note}\n"
            f"Confidence: {round(result.confidence, 2)}\n"
        )

        if result.complexity_reason:
            response += f"Reason: {result.complexity_reason}\n"

        if intent == "transfer":
            response = await self.transfer.run_simple(phone_number, text)
        elif intent in ("airtime", "data"):
            response += "Next: begin airtime/data flow."
        elif intent == "conversational":
            conv = await self.conversation.generate_reply(phone_number, text, result, _user_ctx)
            print(f"Conversation response: {conv}")
            response = conv
        else:
            conv = await self.conversation.generate_reply(phone_number, text, result, _user_ctx)
            response = conv

        asyncio.create_task(self._save_last_response(phone_number, response))

        return response
