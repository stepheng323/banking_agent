"""Flow resume handler - detects and handles resume intent."""

from apps.core.src.agent.orchestrator.pipeline.message_handler import MessageHandler
from apps.core.src.agent.orchestrator.pipeline.message_context import MessageContext
from apps.core.src.agent.orchestrator.models.classification import ClassificationResult
from .service import FlowContextService
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FlowResumeHandler(MessageHandler):
    """
    Handles flow resume requests after mid-flow interrupts.
    
    When user responds "yes" or "continue" after we asked
    "Ready to continue your transfer?", this handler triggers resume.
    """
    
    RESUME_PHRASES = {
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay", 
        "continue", "go ahead", "proceed", "resume",
        "yes please", "let's go", "do it", "yes continue",
        "yes, continue", "yeah continue", "ok continue"
    }
    
    def __init__(self, flow_context_service: FlowContextService):
        self.flow_context = flow_context_service
    
    async def can_handle(self, context: MessageContext) -> bool:
        """
        Can handle if:
        1. There's a paused flow
        2. User's message looks like affirmation/resume intent
        """
        paused = await self.flow_context.get_paused_flow(context.phone_number)
        if not paused:
            return False
        
        # Check if message is a resume intent
        text_lower = context.text.lower().strip()
        
        # Direct match or partial match
        if text_lower in self.RESUME_PHRASES:
            return True
        
        # Check if any resume phrase is in the message
        if any(phrase in text_lower for phrase in self.RESUME_PHRASES):
            return True
        
        # Check classification result
        if context.classification_result:
            intent = context.classification_result.intent
            if intent in ("yes", "confirm", "resume_flow"):
                return True
        
        return False
    
    async def handle(self, context: MessageContext) -> MessageContext:
        """
        Resume the paused flow.
        
        Clear the pause marker and update classification to route to the subgraph.
        """
        paused = await self.flow_context.get_paused_flow(context.phone_number)
        if not paused:
            return context
        
        flow_type = paused.get("flow_type", "")
        
        # Clear the pause marker
        await self.flow_context.clear_paused_flow(context.phone_number)
        
        logger.info("flow_resuming", phone=context.phone_number, flow_type=flow_type)
        
        # Create a new classification result with the flow type as intent
        # This ensures IntentRoutingHandler routes to the correct subgraph
        new_classification = ClassificationResult(
            intent=flow_type,  # 'transfer' or 'airtime'
            is_cancellation=False,
            is_complex=False,
            confidence=0.95,
            response="",
            complexity_reason="Flow resume after interrupt",
        )
        
        return context.update(
            classification_result=new_classification,
            is_flow_resume=True,
        )

