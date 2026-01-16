"""Configuration and dependencies for the OrchestratorAgent."""

from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.account_management.service import AccountManagementService
from apps.core.src.agent.graphs.airtime import AirtimeService
from apps.core.src.agent.graphs.data import DataService
from apps.core.src.agent.graphs.faq import FAQService
from apps.core.src.agent.graphs.query import QueryService
from apps.core.src.agent.graphs.support import SupportService
from apps.core.src.agent.graphs.transfer import TransferService
from apps.core.src.agent.orchestrator.pipeline_stages.quote.service import QuoteService
from apps.core.src.agent.orchestrator.pipeline_stages.task_queue.executor import TaskExecutor
from apps.core.src.agent.orchestrator.registry import ExecutorRegistry
from apps.core.src.agent.orchestrator.services import (
    ConversationResponder,
    TaskQueueService,
)
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories import BeneficiaryRepository, UserRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository


@dataclass
class OrchestratorDependencies:
    """Dependencies required by the OrchestratorAgent."""

    llm: ChatOpenAI
    user_repo: UserRepository
    beneficiary_repo: BeneficiaryRepository
    actionable_message_repo: ActionableMessageRepository
    whatsapp_client: WhatsAppClient
    task_queue_service: TaskQueueService
    conversation_responder: ConversationResponder
    transfer_service: TransferService
    airtime_service: AirtimeService
    task_executor: TaskExecutor
    query_service: QueryService
    account_management_service: AccountManagementService
    executor_registry: ExecutorRegistry | None = None
    quote_service: QuoteService | None = None
    media_service: Any | None = None
    data_service: DataService | None = None
    support_service: SupportService | None = None
    faq_service: FAQService | None = None
