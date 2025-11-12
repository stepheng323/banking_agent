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

    async def _save_classification_result(self, phone_number: str, result: ClassificationResult) -> None:
        """Save classification result to Redis for use by transaction flows."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:last_classification"
            # Save with 1 hour TTL
            await redis_client.set(key, result.model_dump_json(), ex=3600)
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
            "Classify messages into: transfer, airtime, data, conversational, cancel, unknown. "
            "Determine complexity (multi-step reasoning, dynamic amounts, pooling accounts, historical references, multiple transactions). "
            "Work across languages: English, Yoruba, Hausa, Igbo, Nigerian Pidgin, French, and more.\n\n"
            
            "**CANCELLATION INTENT:**\n"
            "- If user wants to cancel, abort, or stop the current transaction, classify as 'cancel'\n"
            "- Cancellation phrases: 'cancel', 'abort', 'stop', 'nevermind', 'forget it', 'don't send', 'no thanks', 'not now'\n"
            "- Multilingual: 'ma fi sile' (Yoruba: forget it), 'ka soke' (Hausa: stop), equivalent phrases in other languages\n"
            "- Set is_cancellation=true when intent is 'cancel'\n"
            "- If there's an active transaction (context shows active_flow and flow_state not in initial states), cancellation is more likely\n"
            "- If user provides transaction details (amount, account, bank), it's NOT cancellation - it's a continuation\n"
            "- If user wants to change/modify transaction details, it's NOT cancellation - classify as the transaction type\n\n"
            
            "**CONTEXT AWARENESS (HIGHEST PRIORITY):**\n"
            "- If context.conversationState exists with active_flow='transfer', classify as 'transfer' (continuation) UNLESS user explicitly cancels\n"
            "- If previous assistant response asked for transfer details, and user provides them, classify as 'transfer'\n"
            "- If user says 'cancel' during an active transfer, classify as 'cancel' with is_cancellation=true\n"
            "- Account numbers (10 digits), bank names, or combinations ('0760505261 Access bank') are transfer continuations (not cancellation)\n"
            "- Short responses to transfer questions are continuations\n\n"
            
            "**EXAMPLES:**\n"
            "- 'cancel' → intent: cancel, is_cancellation: true\n"
            "- 'ma fi sile' (Yoruba: forget it) → intent: cancel, is_cancellation: true\n"
            "- 'stop' → intent: cancel, is_cancellation: true\n"
            "- 'no thanks' → intent: cancel, is_cancellation: true\n"
            "- 'send 5k' → intent: transfer, is_cancellation: false\n"
            "- 'Access bank' → intent: transfer, is_cancellation: false\n"
            "- '0760505261 Access bank' → intent: transfer, is_cancellation: false\n"
            "- '5k' (after being asked for amount) → intent: transfer, is_cancellation: false\n"
            "- 'change amount to 10k' → intent: transfer, is_cancellation: false (modification, not cancellation)\n"
            "- 'hi' → intent: conversational, is_cancellation: false\n"
            "- 'check balance' → intent: conversational, is_cancellation: false\n\n"
            
            "**PRINCIPLE:** If the message answers a question or provides requested information, it's a continuation. "
            "If the message explicitly cancels/aborts, it's cancellation. Otherwise, classify based on intent.\n"
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

        # Save classification result for use by transaction flows
        asyncio.create_task(self._save_classification_result(phone_number, result))

        intent = result.intent.lower()
        
        # Handle cancellation intent
        is_cancellation = (intent == "cancel" or result.is_cancellation is True)
        if is_cancellation:
            # Check if there's an active transaction to cancel
            # First check Redis conversation_state, then check for pending_transfer as fallback
            has_active_transaction = False
            active_flow = None
            flow_state = None
            transfer_status = None
            
            if conversation_state:
                active_flow = conversation_state.get("active_flow")
                flow_state = conversation_state.get("flow_state")
                transfer_status = conversation_state.get("transfer_status")
                
                # Check if there's an active transaction
                if (active_flow and 
                    (flow_state not in ("extracting", "error", "cancelled", None) or
                     transfer_status == "pending")):
                    has_active_transaction = True
            
            # Fallback: Check for pending_transfer in Redis (for cases where conversation_state wasn't updated)
            if not has_active_transaction:
                try:
                    redis_client = RedisClient.get_client()
                    pending_transfer = await redis_client.get(f"user:{phone_number}:pending_transfer")
                    if pending_transfer:
                        has_active_transaction = True
                        active_flow = "transfer"
                        transfer_status = "pending"
                        print(f"✅ Found active transaction via pending_transfer fallback")
                except Exception as e:
                    print(f"⚠️  Error checking pending_transfer: {e}")
            
            if has_active_transaction:
                # Route to the appropriate flow's cancellation handler
                if active_flow == "transfer":
                    # Pass classification result (with cancellation intent) to transfer service
                    # The transfer service will detect this and handle cancellation
                    classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                        "intent": result.intent,
                        "is_cancellation": result.is_cancellation,
                        "confidence": result.confidence,
                    }
                    response = await self.transfer.run_simple(phone_number, text, classification_dict)
                    asyncio.create_task(self._save_last_response(phone_number, response))
                    return response
                # Future: handle airtime/data cancellation
                elif active_flow in ("airtime", "data"):
                    # Future: route to airtime/data cancellation
                    response = "Cancellation for airtime/data flows will be implemented soon."
                    asyncio.create_task(self._save_last_response(phone_number, response))
                    return response
            
            # No active transaction to cancel
            response = "There's no active transaction to cancel."
            asyncio.create_task(self._save_last_response(phone_number, response))
            return response

        complex_note = "complex" if result.is_complex else "simple"

        response = (
            f"Intent: {intent}\n"
            f"Complexity: {complex_note}\n"
            f"Confidence: {round(result.confidence, 2)}\n"
        )

        if result.complexity_reason:
            response += f"Reason: {result.complexity_reason}\n"

        if intent == "transfer":
            # Pass classification result to transfer service for cancellation detection
            classification_dict = result.model_dump() if hasattr(result, 'model_dump') else {
                "intent": result.intent,
                "is_cancellation": result.is_cancellation,
                "confidence": result.confidence,
            }
            response = await self.transfer.run_simple(phone_number, text, classification_dict)
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
