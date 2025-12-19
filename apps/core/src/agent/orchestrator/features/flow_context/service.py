"""Flow context service for pause/resume during interrupts."""

from typing import Any, Optional
import json

from shared.cache.redis_client import RedisClient
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FlowContextService:
    """
    Manages paused flow contexts for seamless mid-flow interrupts.
    
    When user asks "What's my balance?" mid-transfer, we:
    1. Mark the transfer as paused (state is already in LangGraph checkpoint)
    2. Handle the balance query
    3. Offer to resume: "Ready to continue your transfer?"
    """
    
    PAUSE_TTL = 1800  # 30 minutes
    
    async def pause_flow(
        self,
        phone_number: str,
        flow_type: str,
        interrupt_reason: str,
        flow_summary: Optional[dict] = None,
        last_response: Optional[str] = None,
    ) -> None:
        """
        Mark current flow as paused before handling interrupt.
        
        Args:
            phone_number: User's phone
            flow_type: Type of paused flow ('transfer', 'airtime')
            interrupt_reason: Why flow was paused ('balance_query', 'account_list')
            flow_summary: Key details for resume prompt (amount, recipient, etc.)
            last_response: The last question asked before pause (to replay on resume)
        """
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:paused_flow"
            
            # If no last_response provided, try to get it from Redis
            if not last_response:
                last_response_key = f"user:{phone_number}:last_response"
                last_response = await redis_client.get(last_response_key)
            
            data = {
                "flow_type": flow_type,
                "interrupt_reason": interrupt_reason,
                "flow_summary": flow_summary or {},
                "last_response": last_response,
            }
            
            await redis_client.set(key, json.dumps(data), ex=self.PAUSE_TTL)
            logger.info("flow_paused", phone=phone_number, flow_type=flow_type, reason=interrupt_reason)
            
        except Exception as e:
            logger.error("pause_flow_error", phone=phone_number, error=str(e))
    
    async def get_paused_flow(self, phone_number: str) -> Optional[dict]:
        """Get info about the currently paused flow."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:paused_flow"
            data = await redis_client.get(key)
            
            if data:
                return json.loads(data)
            return None
            
        except Exception as e:
            logger.error("get_paused_flow_error", phone=phone_number, error=str(e))
            return None
    
    async def clear_paused_flow(self, phone_number: str) -> None:
        """Clear paused flow after resume or timeout."""
        try:
            redis_client = RedisClient.get_client()
            key = f"user:{phone_number}:paused_flow"
            await redis_client.delete(key)
            logger.info("paused_flow_cleared", phone=phone_number)
            
        except Exception as e:
            logger.error("clear_paused_flow_error", phone=phone_number, error=str(e))
    
    async def generate_resume_prompt(self, phone_number: str) -> Optional[str]:
        """
        Generate a contextual resume prompt after interrupt is handled.
        
        Returns:
            Resume prompt like "Ready to continue your ₦5,000 transfer to Mum?"
            Or None if no paused flow.
        """
        paused = await self.get_paused_flow(phone_number)
        if not paused:
            return None
        
        flow_type = paused.get("flow_type", "")
        summary = paused.get("flow_summary", {})
        
        if flow_type == "transfer":
            amount = summary.get("amount")
            recipient = summary.get("recipient_name") or summary.get("recipient_account", "")
            
            if amount and recipient:
                return f"Ready to continue your ₦{amount:,.0f} transfer to {recipient}?"
            elif amount:
                return f"Ready to continue your ₦{amount:,.0f} transfer?"
            else:
                return "Ready to continue your transfer?"
                
        elif flow_type == "airtime":
            amount = summary.get("amount")
            phone = summary.get("recipient_phone", "")
            
            if amount and phone:
                return f"Ready to continue your ₦{amount:,.0f} airtime for {phone}?"
            elif amount:
                return f"Ready to continue your ₦{amount:,.0f} airtime purchase?"
            else:
                return "Ready to continue buying airtime?"
        
        return "Ready to continue where you left off?"
