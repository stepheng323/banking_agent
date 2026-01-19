"""Workflow task handlers package."""

from apps.core.src.agent.workflow.handlers.base import (
    success_result,
    needs_input_result,
    failed_result,
)
from apps.core.src.agent.workflow.handlers.transfer import TransferTaskHandler
from apps.core.src.agent.workflow.handlers.query import QueryTaskHandler
from apps.core.src.agent.workflow.handlers.airtime import AirtimeTaskHandler
from apps.core.src.agent.workflow.handlers.data import DataTaskHandler
from apps.core.src.agent.workflow.handlers.account_management import AccountManagementTaskHandler
from apps.core.src.agent.workflow.handlers.support import SupportTaskHandler
from apps.core.src.agent.workflow.handlers.faq import FAQTaskHandler


__all__ = [
    "success_result",
    "needs_input_result",
    "failed_result",
    "TransferTaskHandler",
    "QueryTaskHandler",
    "AirtimeTaskHandler",
    "DataTaskHandler",
    "AccountManagementTaskHandler",
    "SupportTaskHandler",
    "FAQTaskHandler",
]
