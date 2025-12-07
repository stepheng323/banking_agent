"""Parser for account management intents using LLM."""

import json
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage


class AccountManagementIntent(BaseModel):
    """Structured output for account management intent."""
    
    action: str = Field(
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
    
    def __init__(self, llm: Runnable):
        self.llm = llm
    
    async def parse(self, text: str) -> Dict[str, Any]:
        """
        Parse user text into structured intent.
        
        Args:
            text: User's input text
            
        Returns:
            Dictionary with action and identifier
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
            "- 'Make my Opay default' -> identifier: 'Opay'\n\n"
            
            "Return ONLY a JSON object with keys: 'action', 'identifier', 'language'."
        )
        
        try:
            response = await self.llm.ainvoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ])
            
            content = response.content if isinstance(response, AIMessage) else str(response)
            
            # Clean up content to ensure valid JSON
            content = content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            return data
            
        except Exception as e:
            print(f"Error parsing account management intent: {e}")
            return {"action": "list", "identifier": None}

