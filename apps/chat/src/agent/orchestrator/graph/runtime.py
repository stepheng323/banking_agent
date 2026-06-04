"""Typed runtime configuration for orchestrator graph invocations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from langchain_core.runnables import RunnableConfig

from apps.chat.src.agent.orchestrator.conversation.conversation_responder import ConversationResponder
from apps.chat.src.agent.orchestrator.graph.progress import TurnProgressTracker
from apps.chat.src.agent.orchestrator.planning.task_planner import TaskPlanner
from banking.accounts.repositories.account_repository import AccountRepository
from banking.beneficiaries.repositories.beneficiary_repository import BeneficiaryRepository
from banking.beneficiaries.services.suggestion_service import BeneficiarySuggestionService
from banking.identity.repositories.user_repository import UserRepository
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.runtime.protocols import WorkerProtocol
from shared.clients.abstractions.banking import BankDataProvider
from shared.queue.adapter import QueuePublisher


@dataclass(frozen=True)
class GraphConfigDependencies:
    """Dependencies exposed to LangGraph nodes through RunnableConfig."""

    task_planner: TaskPlanner
    services: Mapping[str, WorkerProtocol]
    user_repo: UserRepository
    beneficiary_repo: BeneficiaryRepository
    account_repo: AccountRepository
    actionable_message_repo: ActionableMessageRepository
    banking_provider: BankDataProvider
    beneficiary_suggestion_service: BeneficiarySuggestionService
    redis_client: redis.Redis
    publisher: QueuePublisher
    conversation_responder: ConversationResponder | None


@dataclass(frozen=True)
class GraphRunnableConfig:
    """RunnableConfig plus typed values needed by the graph handler."""

    config: RunnableConfig
    thread_id: str


def graph_thread_id(phone_number: str, channel: str) -> str:
    return f"{channel}:{phone_number}"


def build_graph_runnable_config(
    *,
    phone_number: str,
    channel: str,
    dependencies: GraphConfigDependencies,
    progress_tracker: TurnProgressTracker | None = None,
) -> GraphRunnableConfig:
    thread_id = graph_thread_id(phone_number, channel)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "task_planner": dependencies.task_planner,
        "services": dependencies.services,
        "user_repo": dependencies.user_repo,
        "beneficiary_repo": dependencies.beneficiary_repo,
        "account_repo": dependencies.account_repo,
        "actionable_message_repo": dependencies.actionable_message_repo,
        "banking_provider": dependencies.banking_provider,
        "beneficiary_suggestion_service": dependencies.beneficiary_suggestion_service,
        "redis_client": dependencies.redis_client,
        "publisher": dependencies.publisher,
        "conversation_responder": dependencies.conversation_responder,
    }
    if progress_tracker is not None:
        configurable["progress_tracker"] = progress_tracker

    return GraphRunnableConfig(
        config={
            "configurable": configurable,
            "recursion_limit": 50,
        },
        thread_id=thread_id,
    )


__all__ = [
    "GraphConfigDependencies",
    "GraphRunnableConfig",
    "build_graph_runnable_config",
    "graph_thread_id",
]
