"""Beneficiary suggestion response handler for the orchestrator."""

import asyncio
import traceback
from typing import Any

from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.pipeline_stages.context_loader.service import OrchestratorContextManager
from shared.cache.redis_client import RedisClient
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)

TRANSACTION_INTENTS = {"transfer", "airtime", "data"}
BENEFICIARY_RESPONSE_INTENTS = {"yes", "no", "confirm", "skip", "proceed", "cancel"}


class OrchestratorBeneficiaryHandler:
    """Handles beneficiary suggestion responses."""

    def __init__(self, context_manager: OrchestratorContextManager) -> None:
        self.context_manager = context_manager

    async def handle_beneficiary_response(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult,
        suggestion_context: dict,
    ) -> str | None:
        """Handle user response to beneficiary suggestion."""
        intent = result.intent.lower()
        has_alias = bool(result.extracted_alias)
        beneficiary_type = suggestion_context.get("beneficiary_type", "transfer")

        if has_alias:
            return await self._handle_alias_provided(
                phone_number, result, suggestion_context, beneficiary_type
            )
        excluded_intents = {
            *TRANSACTION_INTENTS,
            "repeat_transaction",
            "modify_transaction",
            "manage_accounts",
            "query_balance",
            "explanation",
        }
        if intent in excluded_intents:
            return None

        if intent in ("yes", "confirm", "proceed"):
            return await self._handle_confirm(
                phone_number, result, suggestion_context, beneficiary_type
            )
        elif intent in ("no", "skip", "cancel"):
            return await self._handle_decline(phone_number)

        text_clean = text.strip()
        forbidden_chars = ['http', '@', '#', '/']
        forbidden_keywords = {'again', 'repeat', 'menu', 'home', 'back', 'restart', 'help'}
        
        if (
            len(text_clean) > 0
            and len(text_clean) <= 50
            and not any(char in text_clean for char in forbidden_chars)
            and text_clean.lower() not in forbidden_keywords
        ):
            return await self._save_beneficiary(
                phone_number, suggestion_context, beneficiary_type, text_clean[:64]
            )

        return None

    async def _handle_confirm(
        self,
        phone_number: str,
        result: ClassificationResult,
        suggestion_context: dict,
        beneficiary_type: str,
    ) -> str | None:
        """Handle 'yes/confirm' response to save beneficiary."""
        alias = result.extracted_alias

        if not alias:
            alias = suggestion_context.get("alias_suggested") or suggestion_context.get("original_alias")

        if beneficiary_type in ("airtime", "data") and not alias:
            return await self._request_alias(phone_number, suggestion_context)

        return await self._save_beneficiary(phone_number, suggestion_context, beneficiary_type, alias)

    async def _handle_decline(self, phone_number: str) -> str | None:
        """Handle 'no/skip' response."""
        try:
            redis_client = RedisClient.get_client()
            await redis_client.delete(f"user:{phone_number}:beneficiary_suggestion")
            response = "Got it. I won't save this recipient as a beneficiary."
            asyncio.create_task(self.context_manager.save_last_response(phone_number, response))
            return response
        except Exception:
            return None

    async def _handle_alias_provided(
        self,
        phone_number: str,
        result: ClassificationResult,
        suggestion_context: dict,
        beneficiary_type: str,
    ) -> str | None:
        """Handle when user provides an alias name."""
        alias = result.extracted_alias
        if not alias or not alias.strip():
            return None

        return await self._save_beneficiary(phone_number, suggestion_context, beneficiary_type, alias[:64])

    async def _request_alias(self, phone_number: str, suggestion_context: dict) -> str:
        """Ask user to provide an alias for airtime/data beneficiaries."""
        recipient_phone = suggestion_context.get("phone_number", "")
        network = suggestion_context.get("network", "")
        masked_phone = f"…{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone

        if network:
            response = f"Please provide a name or alias to save {masked_phone} ({network}) as a beneficiary. For example, reply with 'Mum' or 'Home'."
        else:
            response = f"Please provide a name or alias to save {masked_phone} as a beneficiary."

        asyncio.create_task(self.context_manager.save_last_response(phone_number, response))
        return response

    async def _save_beneficiary(
        self,
        phone_number: str,
        suggestion_context: dict,
        beneficiary_type: str,
        alias: str | None,
    ) -> str | None:
        """Save beneficiary to database."""
        redis_client = RedisClient.get_client()
        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"

        try:
            with UnitOfWork() as uow:
                if not uow.users or not uow.beneficiaries:
                    return await self._error_response(phone_number, "Sorry, I couldn't process that. Please try again.")

                user = uow.users.get_by_phone(phone_number)
                if not user:
                    return await self._error_response(phone_number, "User not found. Please contact support.")

                if beneficiary_type in ("airtime", "data"):
                    created = await self._create_airtime_beneficiary(uow, user, suggestion_context, alias)
                    if isinstance(created, str):
                        return await self._error_response(phone_number, created)
                else:
                    self._create_transfer_beneficiary(uow, user, suggestion_context, alias)

                uow.commit()

            await redis_client.delete(suggestion_key)
            await self._cleanup_session(phone_number, beneficiary_type)

            if alias:
                response = f"✓ Saved as '{alias}'. You can now use this alias next time."
            else:
                recipient_name = suggestion_context.get("recipient_name", "recipient")
                response = f"✓ Saved {recipient_name} as a beneficiary."

            asyncio.create_task(self.context_manager.save_last_response(phone_number, response))
            return response

        except Exception:
            logger.error("error_creating_beneficiary")
            traceback.print_exc()
            return None

    async def _create_airtime_beneficiary(
        self,
        uow: UnitOfWork,
        user: Any,
        suggestion_context: dict,
        alias: str | None,
    ) -> bool | str:
        """Create airtime/data beneficiary. Returns True on success, error string on failure."""
        if not alias:
            recipient_phone = suggestion_context.get("phone_number", "")
            masked = f"…{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
            return f"Please provide a name or alias to save {masked} as a beneficiary."

        recipient_phone = suggestion_context.get("phone_number", "")
        network = suggestion_context.get("network", "")

        if recipient_phone and not network:
            from apps.core.src.agent.graphs.airtime.graph.nodes.extraction import detect_network_from_phone
            network = detect_network_from_phone(recipient_phone)
            if not network:
                return f"Could not detect network for {recipient_phone}. Please provide a valid Nigerian phone number."

        if not network:
            return "Network information is required to save this beneficiary."

        beneficiary_type = suggestion_context.get("beneficiary_type", "airtime")
        uow.beneficiaries.create(
            user_id=str(user.id),
            beneficiary_type=beneficiary_type,
            account_name=alias,
            account_number=recipient_phone,
            bank_code=None,
            bank_name=network,
            alias=alias,
        )
        return True

    def _create_transfer_beneficiary(
        self,
        uow: UnitOfWork,
        user: Any,
        suggestion_context: dict,
        alias: str | None,
    ) -> None:
        """Create transfer beneficiary."""
        uow.beneficiaries.create(
            user_id=str(user.id),
            beneficiary_type="transfer",
            account_name=suggestion_context.get("recipient_name", ""),
            account_number=suggestion_context.get("account_number", ""),
            bank_code=suggestion_context.get("bank_code", ""),
            bank_name=suggestion_context.get("bank_name", ""),
            alias=alias,
        )

    async def _cleanup_session(self, phone_number: str, beneficiary_type: str) -> None:
        """Clear session state after saving."""
        try:
            redis_client = RedisClient.get_client()
            await redis_client.delete(f"user:{phone_number}:conversation_state")

            session_key = (
                f"user:{phone_number}:airtime_session_start"
                if beneficiary_type in ("airtime", "data")
                else f"user:{phone_number}:transfer_session_start"
            )
            await redis_client.delete(session_key)

            await redis_client.delete(f"user:{phone_number}:pending_transfer")
            await redis_client.delete(f"user:{phone_number}:pending_transfer_flow_token")

        except Exception:
            pass

    async def _error_response(self, phone_number: str, message: str) -> str:
        """Send error response and return it."""
        asyncio.create_task(self.context_manager.save_last_response(phone_number, message))
        return message
