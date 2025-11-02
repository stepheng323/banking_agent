"""Intent disambiguation service for multi-recipient scenarios."""
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from apps.core.src.agent.models.user_context import UserContext
from apps.core.src.agent.models.corrected_intent import DisambiguatedIntent


class IntentDisambiguator:
    """
    Disambiguates multi-recipient and multi-account transfer instructions.
    """
    
    def __init__(self, llm: ChatOpenAI):
        """Initialize with LLM."""
        self.llm = llm.with_structured_output(DisambiguatedIntent)
    
    async def disambiguate(
        self,
        message: str,
        user_context: UserContext
    ) -> DisambiguatedIntent:
        """
        Disambiguate multi-recipient transfers.
        
        Args:
            message: Corrected message (typos already fixed)
            user_context: User context with accounts and beneficiaries
            
        Returns:
            DisambiguatedIntent with clarity on amounts and recipients
        """
        prompt = self._build_disambiguation_prompt(message, user_context)
        result = await self.llm.ainvoke([HumanMessage(content=prompt)])
        
        # Handle dict response
        if isinstance(result, dict):
            result = DisambiguatedIntent(**result)
        
        return result
    
    def _build_disambiguation_prompt(self, message: str, context: UserContext) -> str:
        """Build disambiguation prompt."""
        
        total_balance = context.get_total_balance()
        
        return f"""You are disambiguating a banking instruction for multi-recipient transfers.

USER MESSAGE (typos already corrected):
"{message}"

USER CONTEXT:
- Total available balance: ₦{total_balance:,.2f}
- Number of accounts: {len(context.accounts)}
- Known beneficiaries: {', '.join([b.get('name', '') for b in context.beneficiaries])}

DISAMBIGUATION PATTERNS:

1. "Send X to A and B" → AMBIGUOUS
   - Could mean: X to each person (total X×2)
   - Could mean: X split between them (X/2 each)
   - ASK: "Should I split ₦X between A and B (₦X/2 each), or send ₦X to each (₦X×2 total)?"

2. "Send X to A and Y to B" → CLEAR
   - Explicit amounts for each
   - No clarification needed

3. "Send X equally to A, B, C" → CLEAR
   - "equally" keyword indicates split
   - X/3 to each person

4. "Split X between A and B" → CLEAR
   - "split" keyword indicates divide
   - X/2 to each

5. "Send X to A, also send Y to B" → CLEAR
   - Separate instructions
   - X to A, Y to B

RULES:
- If amounts are explicit for each recipient → NOT ambiguous
- If "equally", "split", "divide" mentioned → NOT ambiguous (split mode)
- If "each" mentioned → NOT ambiguous (amount to each)
- If multiple recipients with ONE amount and NO split keyword → AMBIGUOUS
- Limit to 5 recipients maximum

Return:
- is_ambiguous: true/false
- needs_clarification: true if ambiguous
- clarification_question: if ambiguous
- recipients: [{{"name": str, "amount": float}}] if clear
- split_strategy: "equal" | "explicit" | null
- normalized_instruction: clear rewrite

If the message is not about transfers at all, return is_multi_recipient=false.
"""

