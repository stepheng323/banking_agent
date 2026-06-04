"""Typed active-session stack mutations for execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from apps.chat.src.agent.orchestrator.models.domain import ActiveSession
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.workflows.execution.context import ExecutionTurnContext

SessionDomain = Literal["query", "support", "transfer"]
SessionState = Literal["WAITING_FOR_INPUT", "WAITING_FOR_AUTH", "RUNNING"]
SessionInterruptPolicy = Literal["BLOCK", "CONFIRM", "ALLOW"]


@dataclass(frozen=True)
class ExecutionSessionStack:
    """Read-only facade over the persisted active-session stack."""

    state: OrchestratorState

    @property
    def entries(self) -> list[ActiveSession]:
        return list(self.state.session_stack)


def execution_session_stack(state: OrchestratorState) -> ExecutionSessionStack:
    return ExecutionSessionStack(state)


def upsert_active_session(
    ctx: ExecutionTurnContext,
    *,
    domain: SessionDomain,
    state: SessionState,
    interrupt_policy: SessionInterruptPolicy,
    task_id: str,
) -> None:
    stack = execution_session_stack(ctx.state).entries
    if stack and stack[-1].domain == domain:
        stack[-1].state = state
    else:
        stack.append(
            ActiveSession(
                domain=domain,
                state=state,
                interrupt_policy=interrupt_policy,
                resume_hint={"task_id": task_id},
            )
        )
    ctx.accumulator.set_session_stack(stack)


def pop_active_session(ctx: ExecutionTurnContext, *, domain: SessionDomain) -> None:
    stack = execution_session_stack(ctx.state).entries
    if stack and stack[-1].domain == domain:
        stack.pop()
        ctx.accumulator.set_session_stack(stack)


__all__ = [
    "SessionDomain",
    "SessionInterruptPolicy",
    "SessionState",
    "ExecutionSessionStack",
    "execution_session_stack",
    "pop_active_session",
    "upsert_active_session",
]
