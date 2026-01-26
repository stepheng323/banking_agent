"""Parser for account management intents."""

from typing import Literal

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from shared.utils.logging import get_logger

logger = get_logger(__name__)


class AccountIntent(BaseModel):
    """Structured output for account intent."""

    action: Literal["list", "check_balance", "set_default", "unlink", "link", "unknown"] = Field(
        description=("The action to perform: 'list', 'check_balance', 'set_default', 'unlink', 'link', or 'unknown'")
    )
    identifier: str | None = Field(
        default=None,
        description="The account identifier (bank name, number, or index) if applicable",
    )
    language: str | None = Field(default="english", description="Detected language of the user")


class AccountParser:
    """Parses natural language into structured account intents."""

    def __init__(self, llm: Runnable):
        self.llm = llm

    async def parse(self, text: str) -> AccountIntent:
        """
        Parse user text into structured intent.

        Args:
            text: User's input text

        Returns:
            AccountIntent object
        """
        system_prompt = (
            "You are an intent parser for a banking assistant's account module. "
            "Your job is to extract the user's intent and relevant parameters from their message. "
            "Support English, Pidgin, Hausa, Yoruba, Igbo, and French.\n\n"
            "**ACTIONS:**\n"
            "1. 'list': User wants to see their linked accounts.\n"
            "   - Examples: 'Show my accounts', 'List accounts', 'Jer kalli asusu na'\n"
            "2. 'check_balance': User wants to see the balance of one or all linked accounts.\n"
            "   - Examples: 'What's my balance', 'overall balance', 'Wetin be my balance', 'Nawa ne balance?'\n"
            "3. 'set_default': User wants to set a specific account as their primary/default account.\n"
            "   - Examples: 'Make GTB my default', 'Set number 1 as default', 'Yi GTB ya zama default'\n"
            "4. 'unlink': User wants to remove/disconnect a linked account.\n"
            "   - Examples: 'Unlink my Access bank', 'Remove account 2', 'Cire asusun UBA'\n"
            "5. 'link': User wants to add/connect a new bank account.\n"
            "   - Examples: 'Link a new account', 'Add another bank', 'Ina so in kara asusu'\n\n"
            "6. 'unknown': Intent is unclear or unrelated to account management.\n\n"
            "**IDENTIFIER:**\n"
            "Extract the bank name, alias, or list index (number) mentioned.\n"
            "- 'Set GTBank as default' -> identifier: 'GTBank'\n"
            "- 'Remove number 2' -> identifier: '2'\n"
            "- 'Make my Opay default' -> identifier: 'Opay'"
        )

        try:
            structured_llm = self.llm.with_structured_output(AccountIntent)

            result = await structured_llm.ainvoke(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
            )

            if isinstance(result, dict):
                return AccountIntent(**result)
            return result

        except Exception as e:
            logger.error("parse_account_error", text=text[:100], error=str(e), exc_info=True)
            return AccountIntent(action="list")
