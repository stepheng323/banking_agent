"""Parser for account management intents."""

from typing import Optional, Literal
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountManagementIntent(BaseModel):
    """Structured output for account management intent."""
    
    action: Literal["list", "set_default", "unlink", "link", "unknown"] = Field(
        description="The action to perform: 'list', 'set_default', 'unlink', 'link', or 'unknown'"
    )
    identifier: Optional[str] = Field(
        default=None,
        description="The account identifier (bank name, number, or index) if applicable"
    )
    language: Optional[str] = Field(
        default="english",
        description="Detected language of the user"
    )


class AccountManagementParser:
    """Parses natural language into structured account management intents."""
    
    def __init__(self, llm: ChatOpenAI):
        self.llm = llm
    
    async def parse(self, text: str) -> AccountManagementIntent:
        """
        Parse user text into structured intent.
        
        Args:
            text: User's input text
            
        Returns:
            AccountManagementIntent object
        """
        system_prompt = (
            "You are an intent parser for a banking assistant's account management module. "
            "Your job is to extract the user's intent and relevant parameters from their message. "
            "Support English, Pidgin, Hausa, Yoruba, Igbo, and French.\n\n"
            
            "**ACTIONS:**\n"
            "1. 'list': User wants to see/check their linked accounts or balance (if implied context).\n"
            "   - Examples: 'Show my accounts', 'List accounts', 'Wetin be my balance', 'Jer kalli asusu na'\n"
            "2. 'set_default': User wants to set a specific account as their primary/default account.\n"
            "   - Examples: 'Make GTB my default', 'Set number 1 as default', 'Yi GTB ya zama default'\n"
            "3. 'unlink': User wants to remove/disconnect a linked account.\n"
            "   - Examples: 'Unlink my Access bank', 'Remove account 2', 'Cire asusun UBA'\n"
            "4. 'link': User wants to add/connect a new bank account.\n"
            "   - Examples: 'Link a new account', 'Add another bank', 'Ina so in kara asusu'\n\n"
            "5. 'unknown': Intent is unclear or unrelated to account management.\n\n"
            
            "**IDENTIFIER:**\n"
            "Extract the bank name, alias, or list index (number) mentioned.\n"
            "- 'Set GTBank as default' -> identifier: 'GTBank'\n"
            "- 'Remove number 2' -> identifier: '2'\n"
            "- 'Make my Opay default' -> identifier: 'Opay'"
        )
        
        try:
            structured_llm = self.llm.with_structured_output(AccountManagementIntent)
            
            result = await structured_llm.ainvoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ])
            
            if isinstance(result, dict):
                return AccountManagementIntent(**result)
            return result
            
        except Exception as e:
            logger.error("parse_account_management_error", text=text[:100], error=str(e), exc_info=True)
            return AccountManagementIntent(action="list")
