"""Run context for transfer flow graph."""

from dataclasses import dataclass
from typing import Optional

from langchain_core.runnables import RunnableConfig


@dataclass
class TransferRunContext:
    """Context passed between graph run helpers."""
    
    phone_number: str
    message: str
    message_id: str
    classification_result: Optional[dict]
    image_data: Optional[str]
    config: RunnableConfig
    
    @property
    def message_lower(self) -> str:
        """Lowercase message for comparisons."""
        return self.message.lower().strip()
    
    @property
    def has_task_params(self) -> bool:
        """Check if classification has task parameters."""
        return bool(
            self.classification_result and 
            "task_parameters" in self.classification_result
        )
    
    def get_task_params(self) -> dict:
        """Get task parameters if available."""
        if self.classification_result and "task_parameters" in self.classification_result:
            return self.classification_result.get("task_parameters", {})
        return {}
    
    def get_classification_intent(self) -> str:
        """Get intent from classification result."""
        if self.classification_result:
            return self.classification_result.get("intent", "").lower()
        return ""
