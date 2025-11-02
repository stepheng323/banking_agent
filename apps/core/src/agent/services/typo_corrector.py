"""Context-aware typo correction service."""
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from apps.core.src.agent.models.user_context import UserContext
from apps.core.src.agent.models.corrected_intent import CorrectedIntent


class ContextAwareTypoCorrector:
    """
    Corrects typos using user-specific entity knowledge.
    """

    def __init__(self, llm: ChatOpenAI):
        """Initialize with LLM."""
        self.llm = llm.with_structured_output(CorrectedIntent)

    async def correct_with_context(
        self,
        message: str,
        user_context: UserContext
    ) -> CorrectedIntent:
        """
        Correct typos using knowledge of user's beneficiaries and accounts.

        Args:
            message: User's raw message with potential typos
            user_context: User-specific context (beneficiaries, accounts, etc.)

        Returns:
            CorrectedIntent with corrections and confidence
        """
        prompt = self._build_correction_prompt(message, user_context)
        result = await self.llm.ainvoke([HumanMessage(content=prompt)])

        if isinstance(result, dict):
            result = CorrectedIntent(**result)

        return result

    def _build_correction_prompt(self, message: str, context: UserContext) -> str:
        """
        Build prompt with user's specific entities.
        """
        beneficiary_list = "None"
        if context.beneficiaries:
            beneficiary_list = "\n".join([
                f"  - {b.get('name', 'Unknown')}" +
                (f" (nickname: {b.get('nickname')})" if b.get(
                    'nickname') else "")
                for b in context.beneficiaries
            ])

        bank_list = "None"
        if context.accounts:
            unique_banks = list(set(a.get('bank_name', '')
                                for a in context.accounts if a.get('bank_name')))
            bank_list = "\n".join([f"  - {bank}" for bank in unique_banks])

        return f"""You are correcting typos in a banking instruction with USER-SPECIFIC context.

USER'S SAVED BENEFICIARIES:
{beneficiary_list}

USER'S BANK ACCOUNTS:
{bank_list}

COMMON NIGERIAN BANKING TERMS:
  - GTBank, GTB → Guaranty Trust Bank
  - First Bank, FBN → First Bank of Nigeria  
  - Access → Access Bank
  - Zenith → Zenith Bank
  - UBA → United Bank for Africa
  - Naira: ₦, N, NGN
  - k = 1,000 (e.g., "5k" = 5,000)
  - thousand = 1,000

USER MESSAGE (with potential typos):
"{message}"

CORRECTION RULES:
1. Match names to user's beneficiaries (handle typos, nicknames, variations)
   - "mumy" → "Mum" (if user has beneficiary "Mum")
   - "dadd" → "Dad" (if user has beneficiary "Dad")  
   - "sissy" → "Sister" (if user has beneficiary "Sister")
   - Match variations: "mummy", "mom" → "Mum"

2. Match bank names to user's accounts
   - "GTbankk" → "GTBank" (if user has GTBank account)
   - "first bankk" → "FirstBank"

3. Normalize amounts
   - "5k" → "₦5,000"
   - "100thousand" → "₦100,000"
   - "5000" → "₦5,000"
   - Preserve "equally", "split", "each" keywords

4. Fix common typos
   - "sedn" → "send"
   - "tranfer" → "transfer"
   - "ballance" → "balance"
   - "equaly" → "equally"

IMPORTANT: 
- Only correct to entities that exist in the user's context!
- If a name doesn't match any beneficiary, set needs_clarification=true
- If confidence < 0.7, set needs_clarification=true
- Keep multi-recipient indicators like "and", "equally", "split"

Return corrections with high confidence only when there's a clear match.
"""
