"""Typed contracts for conversational balance reads."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.types.read import ResponseShape
from shared.utils.bank_aliases import display_bank_name

BalanceAccountScope: TypeAlias = Literal["named", "default", "all", "mentioned", "last_result"]
BalanceOperation: TypeAlias = Literal["value", "total", "breakdown", "compare"]
BalanceScopeOperation: TypeAlias = Literal[
    "preserve",
    "replace",
    "add",
    "remove",
    "recent_two",
    "mentioned",
    "last_result",
    "all",
    "default",
]

MAX_BALANCE_ACCOUNT_REFERENCES = 20


def _normalized_bank_names(names: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = display_bank_name(raw) or raw.strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        normalized.append(name)
    return normalized[:MAX_BALANCE_ACCOUNT_REFERENCES]


class BalanceQueryContract(BaseModel):
    """Resolved account scope and deterministic balance presentation operation."""

    model_config = ConfigDict(extra="forbid")

    account_scope: BalanceAccountScope = "all"
    bank_names: list[str] = Field(default_factory=list, max_length=MAX_BALANCE_ACCOUNT_REFERENCES)
    operation: BalanceOperation = "breakdown"
    response_shape: ResponseShape = "fact_value"

    @model_validator(mode="after")
    def normalize_scope(self) -> BalanceQueryContract:
        self.bank_names = _normalized_bank_names(self.bank_names)
        if self.account_scope == "named" and not self.bank_names:
            raise ValueError("named balance scope requires at least one bank")
        if self.account_scope in {"all", "default"}:
            self.bank_names = []
        self.response_shape = "fact_value" if self.operation in {"value", "total"} else "surface_list"
        return self


class BalanceConversationState(BaseModel):
    """Short-lived discourse state stored with the latest balance read frame."""

    model_config = ConfigDict(extra="forbid")

    focused_bank: str | None = None
    mentioned_banks: list[str] = Field(default_factory=list, max_length=MAX_BALANCE_ACCOUNT_REFERENCES)
    last_result_banks: list[str] = Field(default_factory=list, max_length=MAX_BALANCE_ACCOUNT_REFERENCES)
    last_operation: BalanceOperation = "breakdown"

    @model_validator(mode="after")
    def normalize_banks(self) -> BalanceConversationState:
        self.mentioned_banks = _normalized_bank_names(self.mentioned_banks)
        self.last_result_banks = _normalized_bank_names(self.last_result_banks)
        if self.focused_bank:
            self.focused_bank = display_bank_name(self.focused_bank) or self.focused_bank.strip()
        return self


class BalanceFollowupDelta(BaseModel):
    """Sparse semantic patch for a balance continuation."""

    model_config = ConfigDict(extra="forbid")

    scope_operation: BalanceScopeOperation = "preserve"
    bank_names: list[str] = Field(default_factory=list, max_length=MAX_BALANCE_ACCOUNT_REFERENCES)
    operation: BalanceOperation | None = None
    response_shape: ResponseShape | None = None

    @model_validator(mode="after")
    def normalize_banks(self) -> BalanceFollowupDelta:
        self.bank_names = _normalized_bank_names(self.bank_names)
        return self


def initial_balance_contract(*, bank_name: str | None, response_shape: ResponseShape) -> BalanceQueryContract:
    """Specialize a canonical balance read without reinterpreting user text."""
    if bank_name:
        return BalanceQueryContract(
            account_scope="named",
            bank_names=[bank_name],
            operation="value",
            response_shape=response_shape,
        )
    return BalanceQueryContract(
        account_scope="all",
        operation="total" if response_shape == "fact_value" else "breakdown",
        response_shape=response_shape,
    )


def apply_balance_followup(
    contract: BalanceQueryContract,
    state: BalanceConversationState,
    delta: BalanceFollowupDelta,
) -> BalanceQueryContract | None:
    """Apply a semantic delta; return ``None`` when its contextual scope is unresolved."""
    current = list(contract.bank_names)
    scope: BalanceAccountScope = contract.account_scope

    if delta.scope_operation == "replace":
        if not delta.bank_names:
            return None
        current = list(delta.bank_names)
        scope = "named"
    elif delta.scope_operation == "add":
        current = _normalized_bank_names([*current, *delta.bank_names])
        if not current:
            return None
        scope = "named"
    elif delta.scope_operation == "remove":
        removed = {name.casefold() for name in delta.bank_names}
        current = [name for name in current if name.casefold() not in removed]
        if not current:
            return None
        scope = "named"
    elif delta.scope_operation == "mentioned":
        current = list(state.mentioned_banks)
        if len(current) < 2:
            return None
        scope = "named"
    elif delta.scope_operation == "recent_two":
        current = list(state.mentioned_banks[-2:])
        if len(current) != 2:
            return None
        scope = "named"
    elif delta.scope_operation == "last_result":
        current = list(state.last_result_banks)
        if not current:
            return None
        scope = "named"
    elif delta.scope_operation == "all":
        current = []
        scope = "all"
    elif delta.scope_operation == "default":
        current = []
        scope = "default"

    return BalanceQueryContract(
        account_scope=scope,
        bank_names=current,
        operation=delta.operation or contract.operation,
        response_shape=delta.response_shape or contract.response_shape,
    )


__all__ = [
    "BalanceConversationState",
    "BalanceFollowupDelta",
    "BalanceOperation",
    "BalanceQueryContract",
    "apply_balance_followup",
    "initial_balance_contract",
]
