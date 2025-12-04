"""Beneficiary suggestion response handler for the orchestrator."""

from typing import Optional
import asyncio
import traceback

from shared.repositories.unit_of_work import UnitOfWork
from shared.cache.redis_client import RedisClient
from apps.core.src.agent.models.classification import ClassificationResult
from apps.core.src.agent.orchestrator.features.context.service import OrchestratorContextManager


class OrchestratorBeneficiaryHandler:
    """Handles beneficiary suggestion responses."""

    def __init__(
        self,
        context_manager: OrchestratorContextManager,
    ) -> None:
        self.context_manager = context_manager

    async def handle_beneficiary_response(
        self,
        phone_number: str,
        text: str,
        result: ClassificationResult,
        suggestion_context: dict,
    ) -> Optional[str]:
        """
        Handle user response to beneficiary suggestion.

        Args:
            phone_number: User's phone number
            text: User's message
            result: Classification result
            suggestion_context: Beneficiary suggestion context from Redis

        Returns:
            Response string if handled, None otherwise
        """
        intent = result.intent.lower()
        redis_client = RedisClient.get_client()
        suggestion_key = f"user:{phone_number}:beneficiary_suggestion"
        
        # Transaction intents that should NOT be processed as beneficiary responses
        transaction_intents = {"transfer", "airtime", "data"}
        
        # Strict validation: only process if intent is explicitly a beneficiary response
        # or if LLM extracted an alias (user providing an alias name)
        beneficiary_response_intents = {"yes", "no", "confirm", "skip", "proceed"}
        has_alias = bool(result.extracted_alias)
        
        # Reject transaction intents immediately
        if intent in transaction_intents:
            return None
        
        # Only process if intent is a beneficiary response or LLM extracted an alias
        # Rely on LLM to extract the alias, no pattern matching fallback
        if intent not in beneficiary_response_intents and not has_alias:
            return None
        
        # Get beneficiary type from suggestion context (default to "transfer" for backward compatibility)
        beneficiary_type = suggestion_context.get("beneficiary_type", "transfer")

        if intent in ("yes", "confirm", "proceed"):
            # For airtime/data beneficiaries, require an alias
            alias = result.extracted_alias if result.extracted_alias else None
            if beneficiary_type in ("airtime", "data") and not alias:
                # Ask user to provide an alias/name for airtime beneficiaries
                recipient_phone = suggestion_context.get("phone_number", "")
                network = suggestion_context.get("network", "")
                masked_phone = f"…{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
                response = f"Please provide a name or alias to save {masked_phone} ({network}) as a beneficiary. For example, reply with 'Mum' or 'Home'."
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, response))
                return response
            
            try:
                with UnitOfWork() as uow:
                    if not uow.users or not uow.beneficiaries:
                        response = "Sorry, I couldn't process that. Please try again."
                        asyncio.create_task(
                            self.context_manager.save_last_response(phone_number, response))
                        return response

                    user = uow.users.get_by_phone(phone_number)
                    if not user:
                        response = "User not found. Please contact support."
                        asyncio.create_task(
                            self.context_manager.save_last_response(phone_number, response))
                        return response
                    
                    # Handle airtime/data vs transfer beneficiaries differently
                    if beneficiary_type in ("airtime", "data"):
                        # For airtime/data: account_number = phone, bank_name = network (auto-detected)
                        # REQUIRED: alias must be provided for airtime beneficiaries
                        if not alias:
                            recipient_phone = suggestion_context.get("phone_number", "")
                            masked_phone = f"…{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
                            response = f"Please provide a name or alias to save {masked_phone} as a beneficiary."
                            asyncio.create_task(
                                self.context_manager.save_last_response(phone_number, response))
                            return response
                        
                        recipient_phone = suggestion_context.get("phone_number", "")
                        network = suggestion_context.get("network", "")
                        
                        # Auto-detect network from phone if not provided
                        # Network can always be detected from valid Nigerian phone numbers
                        if recipient_phone and not network:
                            from apps.core.src.agent.airtime.nodes.extraction import detect_network_from_phone
                            auto_network = detect_network_from_phone(recipient_phone)
                            if auto_network:
                                network = auto_network
                            else:
                                # Network detection failed - this shouldn't happen for valid Nigerian numbers
                                response = f"Could not detect network for {recipient_phone}. Please provide a valid Nigerian phone number."
                                asyncio.create_task(
                                    self.context_manager.save_last_response(phone_number, response))
                                return response
                        
                        # Ensure network is present before saving
                        if not network:
                            response = f"Network information is required to save this beneficiary."
                            asyncio.create_task(
                                self.context_manager.save_last_response(phone_number, response))
                            return response
                        
                        # Only save phone and alias - network is auto-detected internally
                        # Use None for bank_code (not applicable for airtime/data) instead of empty string
                        uow.beneficiaries.create(
                            user_id=str(user.id),
                            beneficiary_type=beneficiary_type,
                            account_name=alias,  # Use alias as account_name for airtime
                            account_number=recipient_phone,
                            bank_code=None,  # Not used for airtime/data - use NULL instead of empty string
                            bank_name=network,  # Always provided since network is auto-detected
                            alias=alias,
                        )
                    else:
                        # For transfer: use existing fields
                        uow.beneficiaries.create(
                            user_id=str(user.id),
                            beneficiary_type="transfer",
                            account_name=suggestion_context.get(
                                "recipient_name", ""),
                            account_number=suggestion_context.get(
                                "account_number", ""),
                            bank_code=suggestion_context.get("bank_code", ""),
                            bank_name=suggestion_context.get("bank_name", ""),
                            alias=alias,
                        )
                    
                    uow.commit()

                    await redis_client.delete(suggestion_key)

                    try:
                        await redis_client.delete(f"user:{phone_number}:conversation_state")
                        # Clear appropriate session key based on beneficiary type
                        if beneficiary_type == "airtime":
                            await redis_client.delete(f"user:{phone_number}:airtime_session_start")
                        else:
                            await redis_client.delete(f"user:{phone_number}:transfer_session_start")
                    except Exception:
                        pass

                    # Show confirmation message
                    if alias:
                        response = f"✅ Saved as '{alias}'. You can now use this alias next time."
                    else:
                        recipient_name = suggestion_context.get("recipient_name", "recipient")
                        response = f"✅ Saved {recipient_name} as a beneficiary."
                    asyncio.create_task(
                        self.context_manager.save_last_response(phone_number, response))
                    return response
            except Exception as e:
                print(f"Error creating beneficiary: {e}")
                traceback.print_exc()
        elif intent in ("no", "skip", "cancel"):
            # Rely on LLM intent classification, no pattern matching
            try:
                await redis_client.delete(suggestion_key)
                response = "Got it. I won't save this recipient as a beneficiary."
                asyncio.create_task(
                    self.context_manager.save_last_response(phone_number, response))
                return response
            except Exception as e:
                pass
        else:
            # Rely solely on LLM-extracted alias, no pattern matching fallback
            alias_text = result.extracted_alias if result.extracted_alias else None
            
            if not alias_text:
                return None

            if alias_text:
                try:
                    with UnitOfWork() as uow:
                        if not uow.users or not uow.beneficiaries:
                            response = "Sorry, I couldn't process that. Please try again."
                            asyncio.create_task(
                                self.context_manager.save_last_response(phone_number, response))
                            return response
                        user = uow.users.get_by_phone(phone_number)
                        if not user:
                            response = "User not found. Please contact support."
                            asyncio.create_task(
                                self.context_manager.save_last_response(phone_number, response))
                            return response
                        
                        # Handle airtime/data vs transfer beneficiaries differently
                        if beneficiary_type in ("airtime", "data"):
                            # For airtime/data: account_number = phone, bank_name = network (auto-detected)
                            # REQUIRED: alias must be provided for airtime beneficiaries
                            if not alias_text or not alias_text.strip():
                                recipient_phone = suggestion_context.get("phone_number", "")
                                masked_phone = f"…{recipient_phone[-4:]}" if len(recipient_phone) >= 4 else recipient_phone
                                response = f"Please provide a name or alias to save {masked_phone} as a beneficiary."
                                asyncio.create_task(
                                    self.context_manager.save_last_response(phone_number, response))
                                return response
                            
                            recipient_phone = suggestion_context.get("phone_number", "")
                            network = suggestion_context.get("network", "")
                            
                            # Auto-detect network from phone if not provided
                            # Network can always be detected from valid Nigerian phone numbers
                            if recipient_phone and not network:
                                from apps.core.src.agent.airtime.nodes.extraction import detect_network_from_phone
                                auto_network = detect_network_from_phone(recipient_phone)
                                if auto_network:
                                    network = auto_network
                                else:
                                    # Network detection failed - this shouldn't happen for valid Nigerian numbers
                                    response = f"Could not detect network for {recipient_phone}. Please provide a valid Nigerian phone number."
                                    asyncio.create_task(
                                        self.context_manager.save_last_response(phone_number, response))
                                    return response
                            
                            # Ensure network is present before saving
                            if not network:
                                response = f"Network information is required to save this beneficiary."
                                asyncio.create_task(
                                    self.context_manager.save_last_response(phone_number, response))
                                return response
                            
                            # Only save phone and alias - network is auto-detected internally
                            # Use None for bank_code (not applicable for airtime/data) instead of empty string
                            uow.beneficiaries.create(
                                user_id=str(user.id),
                                beneficiary_type=beneficiary_type,
                                account_name=alias_text[:64],  # Use alias as account_name for airtime
                                account_number=recipient_phone,
                                bank_code=None,  # Not used for airtime/data - use NULL instead of empty string
                                bank_name=network,  # Always provided since network is auto-detected
                                alias=alias_text[:64],
                            )
                        else:
                            # For transfer: use existing fields
                            uow.beneficiaries.create(
                                user_id=str(user.id),
                                beneficiary_type="transfer",
                                account_name=suggestion_context.get(
                                    "recipient_name", ""),
                                account_number=suggestion_context.get(
                                    "account_number", ""),
                                bank_code=suggestion_context.get(
                                    "bank_code", ""),
                                bank_name=suggestion_context.get(
                                    "bank_name", ""),
                                alias=alias_text[:64],
                            )
                        
                        uow.commit()
                        await redis_client.delete(suggestion_key)
                        try:
                            await redis_client.delete(f"user:{phone_number}:conversation_state")
                            # Clear appropriate session key based on beneficiary type
                            if beneficiary_type in ("airtime", "data"):
                                await redis_client.delete(f"user:{phone_number}:airtime_session_start")
                            else:
                                await redis_client.delete(f"user:{phone_number}:transfer_session_start")
                        except Exception:
                            pass
                        response = f"✅ Saved as '{alias_text}'. You can now use this alias next time."
                        asyncio.create_task(
                            self.context_manager.save_last_response(phone_number, response))
                        return response
                except Exception as e:
                    print(f"Error creating beneficiary: {e}")
                    traceback.print_exc()

        return None

