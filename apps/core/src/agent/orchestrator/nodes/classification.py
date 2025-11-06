"""Quick intent classification node for early routing optimization."""

import json
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from apps.core.src.agent.orchestrator.state import OrchestratorState


class QuickIntent(BaseModel):
    """Quick intent classification result."""
    intent: Literal["conversational", "query", "transfer", "utility"]
    confidence: float  # 0.0-1.0


class QuickIntentClassifierNode:
    """
    Fast intent classifier that detects conversational messages early
    to skip expensive normalization steps.

    Uses keyword matching for obvious cases (fast) and lightweight LLM
    for edge cases.
    """

    # Obvious conversational patterns
    CONVERSATIONAL_KEYWORDS = {
        "greetings": ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "gm", "sup", "what's up"],
        "thanks": ["thanks", "thank you", "thank", "appreciate", "cheers", "much appreciated"],
        "goodbye": ["bye", "goodbye", "see you", "later", "have a good day", "take care"],
        "acknowledgments": ["ok", "okay", "alright", "got it", "understood", "cool"],
        "help": ["help", "what can you do", "what do you do", "capabilities", "features"]
    }

    # Banking intent keywords (if detected, likely not conversational)
    BANKING_KEYWORDS = {
        "query": ["balance", "check balance", "account", "statement", "balance", "how much"],
        "transfer": ["send", "transfer", "pay", "send money", "to", "recipient", "beneficiary"],
        "utility": ["airtime", "data", "top up", "recharge", "bundle"]
    }

    def __init__(self, llm: ChatOpenAI | None = None):
        """Initialize with optional LLM for edge cases."""
        self.llm = llm
        self.classifier_llm = llm  # use raw LLM; we'll parse compact JSON

    async def _check_pending_pin(self, phone_number: str) -> bool:
        """Check if TransferAgent has a pending PIN confirmation."""
        try:
            from apps.core.src.agent.banking.transfer.transfer_agent import TransferAgent
            agent = TransferAgent()
            await agent._ensure_checkpointer()
            config = {"configurable": {"thread_id": f"TransferAgent:{phone_number}"}}
            checkpoint = await agent.graph.aget_state(config)
            if checkpoint and checkpoint.values:
                values = checkpoint.values
                is_waiting = (
                    values.get("awaiting_clarification")
                    and values.get("clarification_type") == "pin_confirmation"
                )
                return bool(is_waiting)
        except Exception as exc:
            print(f"   ⚠️  Failed to check pending PIN: {exc}")
        return False

    def _keyword_classify(self, message: str) -> QuickIntent | None:
        """Fast keyword-based classification for obvious cases."""
        message_lower = message.lower().strip()

        # Check for obvious conversational patterns
        for category, keywords in self.CONVERSATIONAL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in message_lower:
                    # Double-check it's not part of a banking query
                    if not any(banking_word in message_lower for banking_keywords in self.BANKING_KEYWORDS.values() for banking_word in banking_keywords):
                        return QuickIntent(intent="conversational", confidence=0.95)

        # Check for obvious banking intents
        for intent, keywords in self.BANKING_KEYWORDS.items():
            for keyword in keywords:
                if keyword in message_lower:
                    return QuickIntent(intent=intent, confidence=0.90)

        return None

    async def __call__(self, state: OrchestratorState) -> OrchestratorState:
        """
        Quickly classify intent to optimize routing.

        For obvious conversational messages, skip expensive normalization steps.
        """
        message = state["message"]

        # Check for cancel keywords FIRST (before any other classification)
        message_lower = message.lower().strip()
        cancel_keywords = ["cancel", "abort", "stop", "nevermind", "never mind", "quit", "disregard"]
        if any(kw in message_lower for kw in cancel_keywords):
            # Cancel detected - always use LLM to confirm and get full classification
            print("🔍 Cancel keyword detected, using LLM classification...")
            if self.llm:
                system_prompt = (
                    "You are a strict intent classifier. Return ONLY compact JSON with three keys: \n"
                    "{\n  \"primary_intent\": \"transfer|query|utility|conversational\",\n  \"global_cancel\": true|false,\n  \"new_transfer\": true|false\n}\n\n"
                    "- primary_intent: overall intent of the message;\n"
                    "- global_cancel: true if the user intends to cancel/abort/stop the current operation in ANY language;\n"
                    "- new_transfer: true if the user is asking to start a NEW money transfer instruction."
                )
                user_prompt = f"Classify the message: {message}"
                try:
                    result = await self.classifier_llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ])
                    content = getattr(result, "content", "") or "{}"
                    start = content.find("{")
                    end = content.rfind("}")
                    payload = {}
                    if start != -1 and end != -1 and end > start:
                        payload = json.loads(content[start:end+1])
                    primary = payload.get("primary_intent") or "conversational"
                    state["primary_intent"] = primary
                    state["quick_classification"] = primary
                    # Always set global_cancel=True if cancel keyword found, even if LLM says otherwise
                    state["global_cancel"] = True
                    state["new_transfer"] = bool(payload.get("new_transfer", False))
                    print(f"⚡ LLM classification: {primary}; cancel={state['global_cancel']} (forced True due to cancel keyword); new_transfer={state.get('new_transfer')}")
                    return state
                except Exception as e:
                    print(f"⚠️  Quick classification failed: {e}")
                    # Fallback: if cancel keyword found, assume cancel
                    state["primary_intent"] = "conversational"
                    state["quick_classification"] = "conversational"
                    state["global_cancel"] = True
                    state["new_transfer"] = False
                    print(f"⚡ Fallback: cancel keyword detected, setting global_cancel=True")
                    return state
            
            # No LLM available - still mark as cancel
            state["primary_intent"] = "conversational"
            state["quick_classification"] = "conversational"
            state["global_cancel"] = True
            state["new_transfer"] = False
            print(f"⚡ No LLM: cancel keyword detected, setting global_cancel=True")
            return state

        # Fast keyword check first
        keyword_result = self._keyword_classify(message)

        if keyword_result:
            print(
                f"⚡ Quick classification: {keyword_result.intent} ({keyword_result.confidence:.0%} confidence)")
            state["primary_intent"] = keyword_result.intent
            state["quick_classification"] = keyword_result.intent
            
            # If it's a transfer intent, check TransferAgent checkpoint for pending PIN
            # This ensures we catch pending PIN even if orchestrator continuation check didn't
            if keyword_result.intent == "transfer":
                phone_number = state["phone_number"]
                has_pending_pin = await self._check_pending_pin(phone_number)
                
                if has_pending_pin:
                    print("   🔍 Transfer intent detected + pending PIN found in checkpoint, using LLM to classify...")
                    if self.llm:
                        system_prompt = (
                            "You are a strict intent classifier. Return ONLY compact JSON with three keys: \n"
                            "{\n  \"primary_intent\": \"transfer|query|utility|conversational\",\n  \"global_cancel\": true|false,\n  \"new_transfer\": true|false\n}\n\n"
                            "- primary_intent: overall intent of the message;\n"
                            "- global_cancel: true if the user intends to cancel/abort/stop the current operation in ANY language;\n"
                            "- new_transfer: true if the user is asking to start a NEW money transfer instruction."
                        )
                        user_prompt = f"Classify the message: {message}"
                        try:
                            result = await self.classifier_llm.ainvoke([
                                SystemMessage(content=system_prompt),
                                HumanMessage(content=user_prompt),
                            ])
                            content = getattr(result, "content", "") or "{}"
                            start = content.find("{")
                            end = content.rfind("}")
                            payload = {}
                            if start != -1 and end != -1 and end > start:
                                payload = json.loads(content[start:end+1])
                            state["global_cancel"] = bool(payload.get("global_cancel", False))
                            state["new_transfer"] = bool(payload.get("new_transfer", True))  # Default True if pending PIN found
                            print(f"   ⚡ LLM override: cancel={state['global_cancel']}; new_transfer={state.get('new_transfer')}")
                        except Exception as e:
                            print(f"   ⚠️  LLM check failed: {e}, defaulting to new_transfer=True")
                            # On transfer intent with pending PIN, assume new transfer
                            state["new_transfer"] = True
                            state["global_cancel"] = False
                    else:
                        # No LLM available, assume new transfer if pending PIN
                        state["new_transfer"] = True
                        state["global_cancel"] = False
                else:
                    # No pending PIN - normal flow
                    state["global_cancel"] = False
                    state["new_transfer"] = False
            else:
                # Not transfer intent - normal flow
                state["global_cancel"] = False
                state["new_transfer"] = False
            return state

        # For ambiguous cases, use lightweight LLM classification that ALSO detects global cancel and new transfer
        if self.llm:
            print("🔍 Quick LLM classification (ambiguous case)...")

            system_prompt = (
                "You are a strict intent classifier. Return ONLY compact JSON with three keys: \n"
                "{\n  \"primary_intent\": \"transfer|query|utility|conversational\",\n  \"global_cancel\": true|false,\n  \"new_transfer\": true|false\n}\n\n"
                "- primary_intent: overall intent of the message;\n"
                "- global_cancel: true if the user intends to cancel/abort/stop the current operation in ANY language;\n"
                "- new_transfer: true if the user is asking to start a NEW money transfer instruction."
            )
            user_prompt = f"Classify the message: {message}"

            try:
                result = await self.classifier_llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ])
                content = getattr(result, "content", "") or "{}"
                start = content.find("{")
                end = content.rfind("}")
                payload = {}
                if start != -1 and end != -1 and end > start:
                    payload = json.loads(content[start:end+1])
                primary = payload.get("primary_intent") or "conversational"
                state["primary_intent"] = primary
                state["quick_classification"] = primary
                state["global_cancel"] = bool(payload.get("global_cancel", False))
                state["new_transfer"] = bool(payload.get("new_transfer", False))
                print(f"⚡ LLM classification: {primary}; cancel={state['global_cancel']}; new_transfer={state.get('new_transfer')}")
                return state
            except Exception as e:
                print(f"⚠️  Quick classification failed: {e}")

        # Fallback: assume banking intent, go through full pipeline
        print("⚠️  Could not quickly classify, using full pipeline")
        state["quick_classification"] = None
        state["global_cancel"] = False
        state["new_transfer"] = False
        return state
