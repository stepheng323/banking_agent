"""Typed conversational-set contracts for bounded banking read surfaces.

The models in this module deliberately contain references, filters and review
metadata only.  They never persist hidden repository rows or unmasked account
details.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.money import MoneyAmount
from shared.types.read import ResponseShape

MAX_CONVERSATION_SET_REFERENCES = 20
MAX_REVIEWED_MUTATION_TARGETS = 5

EntitySelectionType: TypeAlias = Literal[
    "beneficiary",
    "schedule",
    "schedule_run",
    "linked_account",
    "support_reference",
    "support_ticket",
    "data_plan",
]
SetScopeOperation: TypeAlias = Literal[
    "preserve",
    "replace",
    "add",
    "remove",
    "recent_two",
    "mentioned",
    "last_result",
    "all",
]
ConversationSetDomain: TypeAlias = Literal[
    "beneficiary",
    "schedule",
    "linked_account",
    "support",
    "data_plan",
]
MutationSetDomain: TypeAlias = Literal["beneficiary", "schedule", "linked_account"]


class EntitySelectionRef(BaseModel):
    """Privacy-safe pointer to one entity shown in a context frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: EntitySelectionType
    entity_id: str = Field(min_length=1, max_length=160)
    frame_id: str = Field(min_length=1, max_length=160)
    display_label: str = Field(min_length=1, max_length=160)
    version_token: str = Field(min_length=1, max_length=80)


class ConversationSetState(BaseModel):
    """Bounded, resumable state for one domain's displayed entity set."""

    model_config = ConfigDict(extra="forbid")

    domain: ConversationSetDomain
    focused_ref: EntitySelectionRef | None = None
    mentioned_refs: list[EntitySelectionRef] = Field(
        default_factory=list,
        max_length=MAX_CONVERSATION_SET_REFERENCES,
    )
    last_result_refs: list[EntitySelectionRef] = Field(
        default_factory=list,
        max_length=MAX_CONVERSATION_SET_REFERENCES,
    )
    active_filters: dict[str, str] = Field(default_factory=dict)
    operation: str | None = Field(default=None, max_length=64)
    created_turn_id: str | None = Field(default=None, max_length=160)
    updated_turn_id: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_references(self) -> ConversationSetState:
        expected_type = {
            "beneficiary": "beneficiary",
            "schedule": "schedule",
            "linked_account": "linked_account",
            "support": "support_reference",
            "data_plan": "data_plan",
        }[self.domain]
        refs = [*self.mentioned_refs, *self.last_result_refs]
        if self.focused_ref is not None:
            refs.append(self.focused_ref)
        if any(ref.entity_type != expected_type for ref in refs):
            raise ValueError("conversation-set references must match their domain")
        if len({ref.entity_id for ref in self.mentioned_refs}) != len(self.mentioned_refs):
            raise ValueError("mentioned references must be unique")
        if len({ref.entity_id for ref in self.last_result_refs}) != len(self.last_result_refs):
            raise ValueError("last-result references must be unique")
        return self


class SetScopeDelta(BaseModel):
    """Sparse semantic scope change returned by an existing follow-up call.

    The LLM may identify visible labels or ordinals, but never chooses database
    records.  Resolution against frame references is deterministic.
    """

    model_config = ConfigDict(extra="forbid")

    operation: SetScopeOperation = "preserve"
    selection_indices: list[int] = Field(
        default_factory=list,
        max_length=MAX_CONVERSATION_SET_REFERENCES,
    )
    target_labels: list[str] = Field(
        default_factory=list,
        max_length=MAX_CONVERSATION_SET_REFERENCES,
    )

    @model_validator(mode="after")
    def validate_targets(self) -> SetScopeDelta:
        if any(index < 1 or index > MAX_CONVERSATION_SET_REFERENCES for index in self.selection_indices):
            raise ValueError("selection indices must address the bounded visible set")
        return self


class SetAmountAllocation(BaseModel):
    """Explicit amount assigned to one visible set member."""

    model_config = ConfigDict(extra="forbid")

    selection_index: int | None = Field(default=None, ge=1, le=MAX_CONVERSATION_SET_REFERENCES)
    target_label: str | None = Field(default=None, max_length=160)
    amount: MoneyAmount = Field(gt=0)

    @model_validator(mode="after")
    def require_selector(self) -> SetAmountAllocation:
        if self.selection_index is None and not self.target_label:
            raise ValueError("allocation requires a visible ordinal or label")
        return self


BeneficiaryOperation: TypeAlias = Literal[
    "count",
    "existence",
    "list",
    "detail",
    "selection",
    "recap",
    "transfer_handoff",
    "delete_review",
]


class BeneficiaryQueryContract(BaseModel):
    """Specialized contract for beneficiary reads and safe handoffs."""

    model_config = ConfigDict(extra="forbid")

    operation: BeneficiaryOperation = "list"
    response_shape: ResponseShape = "surface_list"
    entity_name: str | None = Field(default=None, max_length=120)
    bank_name: str | None = Field(default=None, max_length=120)
    beneficiary_type: Literal["transfer", "airtime", "data"] | None = None
    scope: SetScopeDelta = Field(default_factory=SetScopeDelta)


class BeneficiaryFollowupDelta(BaseModel):
    """Sparse semantic refinement for a retained beneficiary read.

    The operation is authoritative for presentation shape. Optional filters are
    applied only when explicitly represented by the existing frame-follow-up
    interpretation call.
    """

    model_config = ConfigDict(extra="forbid")

    operation: Literal["preserve", "count", "existence", "list", "detail"] = "preserve"
    entity_name: str | None = Field(default=None, max_length=120)
    bank_name: str | None = Field(default=None, max_length=120)
    beneficiary_type: Literal["transfer", "airtime", "data"] | None = None


ScheduleOperation: TypeAlias = Literal[
    "count",
    "existence",
    "list",
    "detail",
    "selection",
    "recap",
    "cancel_review",
    "edit_review",
    "pause_review",
    "resume_review",
]


class ScheduleQueryContract(BaseModel):
    """Specialized contract for scheduled-transaction conversations."""

    model_config = ConfigDict(extra="forbid")

    operation: ScheduleOperation = "list"
    surface: Literal["instructions", "runs"] = "instructions"
    response_shape: ResponseShape = "surface_list"
    recipient_name: str | None = Field(default=None, max_length=120)
    statuses: list[str] = Field(default_factory=list, max_length=8)
    run_statuses: list[Literal["queued", "processing", "successful", "failed"]] = Field(
        default_factory=list,
        max_length=4,
    )
    domains: list[Literal["transfer", "airtime", "data"]] = Field(default_factory=list, max_length=3)
    recurrence: str | None = Field(default=None, max_length=64)
    starts_at: str | None = Field(default=None, max_length=48)
    ends_at: str | None = Field(default=None, max_length=48)
    scope: SetScopeDelta = Field(default_factory=SetScopeDelta)


AccountLifecycleOperation: TypeAlias = Literal[
    "count",
    "existence",
    "list",
    "detail",
    "readiness",
    "default_identity",
    "selection",
    "recap",
    "set_default_handoff",
    "relink_handoff",
    "unlink_review",
]


class AccountLifecycleContract(BaseModel):
    """Specialized contract for linked-account lifecycle conversations."""

    model_config = ConfigDict(extra="forbid")

    operation: AccountLifecycleOperation = "list"
    response_shape: ResponseShape = "surface_list"
    bank_name: str | None = Field(default=None, max_length=120)
    mandate_statuses: list[str] = Field(default_factory=list, max_length=8)
    readiness_statuses: list[str] = Field(default_factory=list, max_length=8)
    default_state: Literal["default", "not_default", "any"] = "any"
    scope: SetScopeDelta = Field(default_factory=SetScopeDelta)


class AccountLifecycleFollowupDelta(BaseModel):
    """Sparse operation and bank-filter refinement for a retained account read."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "preserve",
        "count",
        "existence",
        "list",
        "detail",
        "readiness",
        "default_identity",
    ] = "preserve"
    bank_scope: Literal["preserve", "named", "all"] = "preserve"
    bank_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_bank_scope(self) -> AccountLifecycleFollowupDelta:
        if self.bank_scope == "named" and not self.bank_name:
            raise ValueError("named account scope requires bank_name")
        if self.bank_scope == "all" and self.bank_name is not None:
            raise ValueError("all-account scope cannot carry bank_name")
        return self


class BulkMutationRequest(BaseModel):
    """Bounded ID-backed mutation request prepared for review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: MutationSetDomain
    action: Literal["delete", "cancel", "edit", "pause", "resume", "unlink"]
    targets: list[EntitySelectionRef] = Field(min_length=1, max_length=MAX_REVIEWED_MUTATION_TARGETS)
    patch: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_domain_action(self) -> BulkMutationRequest:
        allowed = {
            "beneficiary": {"delete"},
            "schedule": {"cancel", "edit", "pause", "resume"},
            "linked_account": {"unlink"},
        }
        if self.action not in allowed[self.domain]:
            raise ValueError("bulk mutation action is not valid for its domain")
        expected_type = self.domain
        if any(ref.entity_type != expected_type for ref in self.targets):
            raise ValueError("bulk mutation targets must match their domain")
        return self


class BulkMutationReviewSnapshot(BaseModel):
    """Immutable confirmation snapshot for a reviewed set mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: BulkMutationRequest
    created_turn_id: str | None = Field(default=None, max_length=160)
    created_at_ts: float


class BulkMutationItemOutcome(BaseModel):
    """One itemized result from a reviewed mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: EntitySelectionRef
    outcome: Literal["succeeded", "failed", "stale", "missing", "skipped"]
    message_key: str | None = Field(default=None, max_length=160)


def _normalized_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def resolve_delta_references(
    delta: SetScopeDelta,
    *,
    visible_refs: list[EntitySelectionRef],
) -> tuple[list[EntitySelectionRef], list[str]]:
    """Resolve ordinal/label suggestions against visible stable references.

    Ambiguous labels are returned as unresolved instead of selecting the first
    record.  This is suitable for reads and is required before mutations.
    """

    resolved: list[EntitySelectionRef] = []
    unresolved: list[str] = []
    for index in delta.selection_indices:
        if index > len(visible_refs):
            unresolved.append(str(index))
            continue
        resolved.append(visible_refs[index - 1])

    for label in delta.target_labels:
        needle = _normalized_label(label)
        matches = [
            ref
            for ref in visible_refs
            if needle and needle in _normalized_label(ref.display_label)
        ]
        if len(matches) == 1:
            resolved.append(matches[0])
        else:
            unresolved.append(label)

    deduped = {ref.entity_id: ref for ref in resolved}
    return list(deduped.values()), unresolved


def apply_set_scope(
    state: ConversationSetState,
    delta: SetScopeDelta,
    *,
    resolved_refs: list[EntitySelectionRef] | None = None,
    all_refs: list[EntitySelectionRef] | None = None,
) -> list[EntitySelectionRef]:
    """Apply a resolved scope delta without consulting raw user text."""

    selected = list(resolved_refs or [])
    current = list(state.last_result_refs)
    if delta.operation == "preserve":
        result = selected or ([state.focused_ref] if state.focused_ref is not None else current)
    elif delta.operation == "replace":
        result = selected
    elif delta.operation == "add":
        result = [*current, *selected]
    elif delta.operation == "remove":
        removed = {ref.entity_id for ref in selected}
        result = [ref for ref in current if ref.entity_id not in removed]
    elif delta.operation == "recent_two":
        result = state.mentioned_refs[-2:]
    elif delta.operation == "mentioned":
        result = list(state.mentioned_refs)
    elif delta.operation == "last_result":
        result = current
    else:
        result = list(all_refs or [])

    deduped: dict[str, EntitySelectionRef] = {}
    for ref in result:
        deduped[ref.entity_id] = ref
    return list(deduped.values())[:MAX_CONVERSATION_SET_REFERENCES]


__all__ = [
    "AccountLifecycleContract",
    "AccountLifecycleFollowupDelta",
    "BeneficiaryFollowupDelta",
    "BeneficiaryQueryContract",
    "BulkMutationItemOutcome",
    "BulkMutationRequest",
    "BulkMutationReviewSnapshot",
    "ConversationSetState",
    "EntitySelectionRef",
    "MAX_CONVERSATION_SET_REFERENCES",
    "MAX_REVIEWED_MUTATION_TARGETS",
    "ScheduleQueryContract",
    "SetAmountAllocation",
    "SetScopeDelta",
    "apply_set_scope",
    "resolve_delta_references",
]
