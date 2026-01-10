"""Configuration and dependencies for the OrchestratorAgent."""

from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI

from apps.core.src.agent.orchestrator.services import (
    ConversationResponder,
    TaskExecutor,
    TaskQueueService,
)
from apps.core.src.agent.sub_agents.account_management.service import AccountManagementService
from apps.core.src.agent.sub_agents.airtime import AirtimeService
from apps.core.src.agent.sub_agents.data import DataPurchaseGraph
from apps.core.src.agent.sub_agents.faq import FAQFlowGraph
from apps.core.src.agent.sub_agents.query.graph import QueryFlowGraph
from apps.core.src.agent.sub_agents.support.graph import SupportFlowGraph
from apps.core.src.agent.sub_agents.transfer import TransferService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.repositories import BeneficiaryRepository, UserRepository
from shared.repositories.actionable_message_repository import ActionableMessageRepository

from apps.core.src.agent.orchestrator.registry import ExecutorRegistry


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
    query_graph: QueryFlowGraph
    account_management_service: AccountManagementService
    executor_registry: "ExecutorRegistry | None" = None
    media_service: Any | None = None
    data_graph: DataPurchaseGraph | None = None
    support_graph: SupportFlowGraph | None = None
    faq_graph: FAQFlowGraph | None = None
