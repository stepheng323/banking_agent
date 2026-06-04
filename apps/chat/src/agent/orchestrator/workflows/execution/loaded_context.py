"""Typed loaded-context access for execution orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.chat.src.agent.orchestrator.models.state import OrchestratorState


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


@dataclass(frozen=True)
class ExecutionLoadedContext:
    """Read-only typed facade over the graph state's loaded context."""

    values: Mapping[str, Any]

    def value(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def list_value(self, key: str) -> list[Any]:
        return _list_value(self.value(key, []))

    def dict_value(self, key: str) -> dict[str, Any]:
        return _dict_value(self.value(key, {}))

    @property
    def user_id(self) -> Any:
        return self.value("user_id")

    @property
    def profile(self) -> dict[str, Any]:
        return self.dict_value("profile")

    @property
    def email(self) -> Any:
        return self.profile.get("email")

    @property
    def language(self) -> Any:
        return self.value("language")

    @property
    def accounts(self) -> list[Any]:
        return self.list_value("accounts")

    @property
    def account_rows(self) -> list[dict[str, Any]]:
        return _rows(self.value("accounts", []))

    @property
    def transaction_accounts(self) -> list[Any]:
        return self.list_value("transaction_accounts")

    @property
    def transaction_accounts_or_accounts(self) -> list[Any]:
        if "transaction_accounts" in self.values:
            return self.transaction_accounts
        return self.accounts

    @property
    def transaction_account_rows_or_account_rows(self) -> list[dict[str, Any]]:
        return _rows(self.value("transaction_accounts") or self.value("accounts") or [])

    @property
    def beneficiaries(self) -> list[Any]:
        return self.list_value("beneficiaries")

    @property
    def beneficiary_context_mode(self) -> str:
        return str(self.value("beneficiary_context_mode") or "full")


def loaded_context(state: OrchestratorState) -> ExecutionLoadedContext:
    return ExecutionLoadedContext(state.loaded_context)


def has_loaded_context_value(state: OrchestratorState, key: str) -> bool:
    return key in state.loaded_context


def set_loaded_context_value(state: OrchestratorState, key: str, value: Any) -> None:
    state.loaded_context[key] = value


__all__ = [
    "ExecutionLoadedContext",
    "has_loaded_context_value",
    "loaded_context",
    "set_loaded_context_value",
]
