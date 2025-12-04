"""Account management handler for the orchestrator pipeline."""

import re
from typing import Optional
from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.account_management.service import AccountManagementService


class AccountManagementHandler(MessageHandler):
    """
    Handles account management requests.
    
    This handler processes requests to:
    - List linked accounts
    - Set default account
    - Unlink accounts
    - Link new accounts (guides to onboarding flow)
    """
    
    def __init__(self, account_management_service: AccountManagementService):
        """
        Initialize account management handler.
        
        Args:
            account_management_service: Service for account operations
        """
        self.account_service = account_management_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """Check if this is an account management intent."""
        if not context.classification_result:
            return False
        
        intent = context.classification_result.intent
        
        if intent == "manage_accounts":
            return True
        
        if context.conversation_state.get("awaiting_account_selection"):
            return True
        
        return False
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Handle account management request.
        
        Args:
            context: Message context
            
        Returns:
            Updated context with response
        """
        try:
            user_id = context.user_context.get("profile", {}).get("id")
            message = context.text.lower().strip()
            
            if context.conversation_state.get("awaiting_account_selection"):
                return await self._handle_account_selection(context, user_id, message)
            
            if any(keyword in message for keyword in ["show", "list", "my accounts", "linked accounts"]):
                response = await self.account_service.list_accounts(user_id)
                
                new_state = context.conversation_state.copy()
                new_state["awaiting_account_selection"] = True
                
                return context.update(
                    response=response,
                    handled=True,
                    conversation_state=new_state
                )
            
            if "unlink" in message or "remove" in message or "delete" in message:
                return await self._handle_unlink(context, user_id, message)
            
            if "link" in message or "add" in message or "connect" in message:
                response = (
                    "To link a new bank account, you'll need to complete the Mono Connect flow.\n\n"
                    "This is currently available during onboarding. "
                    "We're working on making this available directly in chat. "
                    "For now, please contact support to link additional accounts."
                )
                return context.update(response=response, handled=True)
            
            # Default: list accounts
            response = await self.account_service.list_accounts(user_id)
            new_state = context.conversation_state.copy()
            new_state["awaiting_account_selection"] = True
            
            return context.update(
                response=response,
                handled=True,
                conversation_state=new_state
            )
            
        except Exception as e:
            print(f"Error in AccountManagementHandler: {e}")
            return context.update(
                response="I'm having trouble managing your accounts right now. Please try again.",
                handled=True
            )
    
    async def _handle_account_selection(
        self,
        context: MessageContext,
        user_id: str,
        message: str
    ) -> MessageContext:
        """Handle account selection by number or bank name."""
        # Clear the awaiting state
        new_state = context.conversation_state.copy()
        new_state.pop("awaiting_account_selection", None)
        
        # Extract identifier (could be number or bank name)
        identifier = self._extract_account_identifier(message)
        
        if identifier:
            response = await self.account_service.set_default(user_id, identifier)
            
            return context.update(
                response=response,
                handled=True,
                conversation_state=new_state
            )
        
        # Couldn't extract identifier, clear state and let other handlers process
        return context.update(
            handled=False,
            conversation_state=new_state
        )
    
    async def _handle_unlink(
        self,
        context: MessageContext,
        user_id: str,
        message: str
    ) -> MessageContext:
        """Handle account unlinking by number or bank name."""
        # Extract identifier (could be number or bank name)
        identifier = self._extract_account_identifier(message)
        
        if identifier:
            response = await self.account_service.unlink_account(user_id, identifier)
            return context.update(response=response, handled=True)
        else:
            # No identifier provided, show list
            response = await self.account_service.list_accounts(user_id)
            response += "\n\n📌 Reply with 'unlink [account number or bank name]' to remove an account."
            
            return context.update(response=response, handled=True)
    
    def _extract_account_identifier(self, message: str) -> Optional[str]:
        """
        Extract account identifier from message.
        Could be a number (1, 2) or bank name (GTB, UBA, Access).
        
        Args:
            message: User message
            
        Returns:
            Account identifier or None
        """
        message_lower = message.lower().strip()
        
        # Try to extract number first
        import re
        number_match = re.search(r'\b(\d+)\b', message)
        if number_match:
            return number_match.group(1)
        
        # Try to extract bank name
        # Common patterns: "unlink gtb", "set uba as default", "make access default"
        bank_keywords = [
            "gtb", "gtbank", "guaranty",
            "uba", "united bank",
            "access",
            "zenith",
            "first bank", "firstbank", "fbn",
            "union",
            "sterling",
            "stanbic",
            "fidelity",
            "wema",
            "polaris",
            "keystone",
            "fcmb",
            "ecobank",
            "providus",
            "kuda",
            "opay",
            "palmpay",
        ]
        
        for bank in bank_keywords:
            if bank in message_lower:
                return bank
        
        return None

