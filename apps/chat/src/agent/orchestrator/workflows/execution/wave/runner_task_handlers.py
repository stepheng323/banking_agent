"""Domain task handler registry for execution waves."""

from apps.chat.src.agent.orchestrator.task_handlers.account_beneficiary import (
    handle_account_task,
    handle_beneficiary_task,
)
from apps.chat.src.agent.orchestrator.task_handlers.purchase import (
    handle_airtime_task,
    handle_data_task,
)
from apps.chat.src.agent.orchestrator.task_handlers.query import handle_query_task
from apps.chat.src.agent.orchestrator.task_handlers.session import handle_orchestrator_task
from apps.chat.src.agent.orchestrator.task_handlers.support import (
    handle_faq_task,
    handle_support_task,
)
from apps.chat.src.agent.orchestrator.task_handlers.transfer import (
    handle_schedule_task,
    handle_transfer_task,
)

_HANDLERS = {
    "transfer": handle_transfer_task,
    "account": handle_account_task,
    "beneficiary": handle_beneficiary_task,
    "airtime": handle_airtime_task,
    "query": handle_query_task,
    "data": handle_data_task,
    "faq": handle_faq_task,
    "support": handle_support_task,
    "schedule": handle_schedule_task,
    "orchestrator": handle_orchestrator_task,
}


__all__ = ["_HANDLERS"]
