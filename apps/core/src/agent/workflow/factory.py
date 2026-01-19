"""Workflow engine factory.

Wires together handlers, registry, and executor.
"""

from apps.core.src.agent.workflow.dag_executor import WorkflowDAGExecutor
from apps.core.src.agent.workflow.handler_registry import HandlerRegistry
from apps.core.src.agent.workflow.handlers.transfer import TransferTaskHandler
from apps.core.src.agent.workflow.handlers.query import QueryTaskHandler
from apps.core.src.agent.workflow.handlers.airtime import AirtimeTaskHandler
from apps.core.src.agent.workflow.handlers.data import DataTaskHandler
from apps.core.src.agent.workflow.handlers.account_management import AccountManagementTaskHandler
from apps.core.src.agent.workflow.handlers.support import SupportTaskHandler
from apps.core.src.agent.workflow.handlers.faq import FAQTaskHandler


def create_workflow_executor(
    transfer_service=None,
    query_service=None,
    airtime_service=None,
    data_service=None,
    account_management_service=None,
    support_service=None,
    faq_service=None,
) -> WorkflowDAGExecutor:
    """
    Create a workflow executor with registered handlers.
    
    Args:
        transfer_service: TransferService instance
        query_service: QueryService instance
        airtime_service: AirtimeService instance
        data_service: DataService instance
        account_management_service: AccountManagementService instance
        support_service: SupportService instance
        faq_service: FAQService instance
    
    Returns:
        Configured WorkflowDAGExecutor
    """
    registry = HandlerRegistry()
    
    if transfer_service:
        registry.register("transfer", TransferTaskHandler(transfer_service))
    
    if query_service:
        registry.register("query", QueryTaskHandler(query_service))
    
    if airtime_service:
        registry.register("airtime", AirtimeTaskHandler(airtime_service))
    
    if data_service:
        registry.register("data", DataTaskHandler(data_service))
    
    if account_management_service:
        registry.register("account_management", AccountManagementTaskHandler(account_management_service))
    
    if support_service:
        registry.register("support", SupportTaskHandler(support_service))
    
    if faq_service:
        registry.register("faq", FAQTaskHandler(faq_service))
    
    return WorkflowDAGExecutor(registry)

