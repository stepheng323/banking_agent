"""Shared services package.

Keep package imports lightweight so app-specific dependencies are loaded
only when those symbols are requested.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "ContextManager",
    "OrchestratorContextManager",
    "ConversationResponder",
    "TaskPlanner",
    "OrchestratorTaskPlanner",
]

if TYPE_CHECKING:
    from shared.receipts.receipt_generator import ReceiptGenerator
    from shared.services.context_manager import ContextManager, OrchestratorContextManager
    from shared.services.conversation_responder import ConversationResponder
    from shared.services.task_planner import OrchestratorTaskPlanner, TaskPlanner


def __getattr__(name: str) -> Any:
    if name == "ReceiptGenerator":
        from shared.receipts.receipt_generator import ReceiptGenerator

        return ReceiptGenerator

    if name in {"ContextManager", "OrchestratorContextManager"}:
        from shared.services.context_manager import ContextManager, OrchestratorContextManager

        exports = {
            "ContextManager": ContextManager,
            "OrchestratorContextManager": OrchestratorContextManager,
        }
        return exports[name]

    if name == "ConversationResponder":
        from shared.services.conversation_responder import ConversationResponder

        return ConversationResponder

    if name in {"TaskPlanner", "OrchestratorTaskPlanner"}:
        from shared.services.task_planner import OrchestratorTaskPlanner, TaskPlanner

        exports = {
            "TaskPlanner": TaskPlanner,
            "OrchestratorTaskPlanner": OrchestratorTaskPlanner,
        }
        return exports[name]

    raise AttributeError(f"module 'shared.services' has no attribute '{name}'")
