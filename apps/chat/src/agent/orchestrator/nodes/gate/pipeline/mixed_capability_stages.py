import re
from dataclasses import dataclass
from typing import Any, Literal

from apps.chat.src.agent.orchestrator.models.domain import TaskSpec, TaskStage
from apps.chat.src.agent.orchestrator.models.state import OrchestratorState
from apps.chat.src.agent.orchestrator.nodes.cancellation import clear_query_session
from apps.chat.src.agent.orchestrator.nodes.gate.pipeline.context import GateContext
from apps.chat.src.agent.orchestrator.nodes.gate.runner import (
    _build_direct_domain_task,
    _build_query_session_exit_updates,
    _classify_obvious_transfer_request,
    _direct_domain_capability_block_message,
    _is_account_balance_request,
    _is_account_domain_request,
    _is_beneficiary_domain_request,
    _is_obvious_airtime_request,
    _is_obvious_data_request,
    _is_query_domain_request,
    _is_structural_query_domain_request,
    _next_direct_account_task_id,
    _route_observability_updates,
)
from shared.i18n.renderer import render_message
from shared.services.unsupported_capabilities import (
    UnsupportedCapability,
    detect_unsupported_capability,
    should_try_semantic_unsupported_capability,
    unsupported_capability_label,
    validate_semantic_unsupported_capability,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)

SupportedDomain = Literal["transfer", "airtime", "data", "account", "beneficiary", "query", "schedule"]

_CLAUSE_SPLIT_RE = re.compile(
    r"(?:\s+(?:and|then|also|plus)\s+|[;\n]+|(?<!\d),(?!\d))",
    re.IGNORECASE,
)
_SCHEDULE_READ_RE = re.compile(r"\b(?:show|list|view|get|check)\b.*\b(?:scheduled|recurring|schedule)\w*\b", re.I)
_SUPPORTED_LABELS: dict[SupportedDomain, str] = {
    "transfer": "money transfer",
    "airtime": "airtime purchase",
    "data": "data purchase",
    "account": "balance or account action",
    "beneficiary": "beneficiary management",
    "query": "transaction query",
    "schedule": "scheduled transaction management",
}
_SUPPORTED_LABELS_BY_LOCALE: dict[str, dict[SupportedDomain, str]] = {
    "pcm": {
        "transfer": "money transfer",
        "airtime": "airtime purchase",
        "data": "data purchase",
        "account": "balance or account action",
        "beneficiary": "beneficiary management",
        "query": "transaction query",
        "schedule": "scheduled transaction management",
    },
    "yo": {
        "transfer": "transfer owo",
        "airtime": "rira airtime",
        "data": "rira data",
        "account": "balance tabi account",
        "beneficiary": "beneficiary management",
        "query": "wiwa transaction",
        "schedule": "scheduled transaction management",
    },
    "ha": {
        "transfer": "transfer kudi",
        "airtime": "sayan airtime",
        "data": "sayan data",
        "account": "balance ko account",
        "beneficiary": "beneficiary management",
        "query": "binciken transaction",
        "schedule": "scheduled transaction management",
    },
    "ig": {
        "transfer": "transfer ego",
        "airtime": "izuta airtime",
        "data": "izuta data",
        "account": "balance ma obu account",
        "beneficiary": "beneficiary management",
        "query": "nyocha transaction",
        "schedule": "scheduled transaction management",
    },
}


@dataclass(frozen=True, slots=True)
class SupportedClause:
    domain: SupportedDomain
    text: str
    heuristic_name: str


@dataclass(frozen=True, slots=True)
class MixedCapabilityMatch:
    supported: tuple[SupportedClause, ...]
    unsupported: tuple[UnsupportedCapability, ...]

    @property
    def is_ambiguous(self) -> bool:
        return len(self.supported) != 1


def _join_labels(labels: list[str]) -> str:
    unique = [label for index, label in enumerate(labels) if label and label not in labels[:index]]
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    if len(unique) == 2:
        return f"{unique[0]} and {unique[1]}"
    return f"{', '.join(unique[:-1])}, and {unique[-1]}"


def _locale_key(locale: str | None) -> str:
    key = (locale or "en").strip().split("-")[0].casefold()
    return key if key in _SUPPORTED_LABELS_BY_LOCALE else "en"


def _supported_label(domain: SupportedDomain, locale: str | None) -> str:
    locale_labels = _SUPPORTED_LABELS_BY_LOCALE.get(_locale_key(locale), {})
    return locale_labels.get(domain, _SUPPORTED_LABELS[domain])


def _split_clauses(text: str | None) -> list[str]:
    return [clause.strip(" \t\r\n.,;:") for clause in _CLAUSE_SPLIT_RE.split(text or "") if clause.strip()]


def _classify_supported_clause(text: str) -> SupportedClause | None:
    normalized = re.sub(r"\s+", " ", text.strip().lower()).rstrip("?.!,")
    if not normalized:
        return None

    if _is_account_balance_request(normalized):
        return SupportedClause("account", text, "balance_request")
    if _is_beneficiary_domain_request(normalized):
        return SupportedClause("beneficiary", text, "beneficiary_list_request")
    if _is_obvious_airtime_request(normalized):
        return SupportedClause("airtime", text, "obvious_airtime_request")
    if _is_obvious_data_request(normalized):
        return SupportedClause("data", text, "obvious_data_request")
    transfer_reason = _classify_obvious_transfer_request(text)
    if transfer_reason in {
        "fresh_transfer_command",
        "fresh_transfer_missing_recipient_command",
        "recipient_bank_details_only",
    }:
        return SupportedClause("transfer", text, transfer_reason)
    if _is_account_domain_request(normalized):
        return SupportedClause("account", text, "account_domain_request")
    if _is_structural_query_domain_request(normalized) or _is_query_domain_request(normalized):
        return SupportedClause("query", text, "query_domain_request")
    if _SCHEDULE_READ_RE.search(normalized):
        return SupportedClause("schedule", text, "schedule_read_request")
    return None


def analyze_mixed_supported_unsupported(text: str | None) -> MixedCapabilityMatch | None:
    supported: list[SupportedClause] = []
    unsupported: list[UnsupportedCapability] = []
    for clause in _split_clauses(text):
        unsupported_capability = detect_unsupported_capability(clause)
        if unsupported_capability is not None:
            if unsupported_capability.key not in {capability.key for capability in unsupported}:
                unsupported.append(unsupported_capability)
            continue
        supported_clause = _classify_supported_clause(clause)
        if supported_clause is not None:
            supported.append(supported_clause)

    if not unsupported or not supported:
        return None
    return MixedCapabilityMatch(supported=tuple(supported), unsupported=tuple(unsupported))


async def _semantic_unsupported_clause(ctx: GateContext, clause: str) -> UnsupportedCapability | None:
    classifier = getattr(ctx.task_planner, "classify_unsupported_capability", None)
    if not callable(classifier) or not should_try_semantic_unsupported_capability(clause):
        return None
    try:
        decision = await classifier(
            ctx.state.phone_number,
            clause,
            locale=ctx.current_locale,
            context="None",
            path_label="direct_path",
        )
    except Exception:
        logger.warning("mixed_capability_semantic_clause_failed")
        return None
    return validate_semantic_unsupported_capability(decision)


async def _analyze_mixed_supported_unsupported_semantic(ctx: GateContext) -> MixedCapabilityMatch | None:
    clauses = _split_clauses(ctx.message_text)
    if len(clauses) < 2:
        return None

    supported: list[SupportedClause] = []
    unsupported: list[UnsupportedCapability] = []
    for clause in clauses:
        unsupported_capability = detect_unsupported_capability(clause)
        if unsupported_capability is not None:
            if unsupported_capability.key not in {capability.key for capability in unsupported}:
                unsupported.append(unsupported_capability)
            continue

        supported_clause = _classify_supported_clause(clause)
        if supported_clause is not None:
            supported.append(supported_clause)
            continue

        semantic_capability = await _semantic_unsupported_clause(ctx, clause)
        if semantic_capability is not None and semantic_capability.key not in {
            capability.key for capability in unsupported
        }:
            unsupported.append(semantic_capability)

    if not unsupported or not supported:
        return None
    return MixedCapabilityMatch(supported=tuple(supported), unsupported=tuple(unsupported))


def mixed_policy_notice(match: MixedCapabilityMatch, *, locale: str) -> str:
    supported_text = _join_labels([_supported_label(item.domain, locale) for item in match.supported])
    unsupported_text = _join_labels([unsupported_capability_label(item, locale) for item in match.unsupported])
    return render_message(
        "planner.mixed_supported_unsupported_notice",
        locale,
        {"supported": supported_text, "unsupported": unsupported_text},
    )


def _temporary_state_with_message(state: OrchestratorState, message_text: str) -> OrchestratorState:
    return state.model_copy(update={"last_message_text": message_text})


def _build_supported_task(state: OrchestratorState, supported: SupportedClause) -> tuple[str, TaskSpec]:
    if supported.domain == "account" and supported.heuristic_name == "balance_request":
        task_id = _next_direct_account_task_id(state.tasks)
        spec = TaskSpec(
            id=task_id,
            type="account",
            stage=TaskStage.DRAFT,
            payload={
                "action": "check_balance",
                "message": state.last_message_text,
                "instruction": state.last_message_text,
            },
        )
        return task_id, spec
    if supported.domain == "schedule":
        return _build_direct_domain_task(
            state=state,
            domain="schedule",
            mode="new",
            schedule_response_mode="list",
        )
    return _build_direct_domain_task(state=state, domain=supported.domain, mode="new")


async def _stage_mixed_supported_unsupported_capability(ctx: GateContext) -> dict[str, Any] | None:
    """Route one supported banking clause while refusing unsupported clauses."""
    if (
        ctx.live_pending_interrupt
        or ctx.state.pending_interrupt is not None
        or ctx.state.has_quote
        or ctx.state.session_stack
        or ctx.state.waves
        or not ctx.phrase_heavy_fastpath_allowed
    ):
        return None

    match = analyze_mixed_supported_unsupported(ctx.message_text)
    if match is None:
        match = await _analyze_mixed_supported_unsupported_semantic(ctx)
    if match is None:
        return None

    notice = mixed_policy_notice(match, locale=ctx.current_locale)
    if match.is_ambiguous:
        logger.info(
            "gate_mixed_capability_ambiguous",
            supported_count=len(match.supported),
            unsupported=[item.key for item in match.unsupported],
        )
        return {
            **ctx.gate_updates,
            "capability_boundary": None,
            "direct_path_triggered": True,
            "final_response": render_message(
                "orchestrator.ambiguity.mixed_supported_unsupported",
                ctx.current_locale,
                {
                    "supported": _join_labels(
                        [_supported_label(item.domain, ctx.current_locale) for item in match.supported]
                    ),
                    "unsupported": _join_labels(
                        [unsupported_capability_label(item, ctx.current_locale) for item in match.unsupported]
                    ),
                },
            ),
            "semantic_path_shape": "mixed_capability_clarify",
            **_route_observability_updates(
                owner="guardrail",
                decision="mixed_supported_unsupported_clarify",
            ),
        }

    supported = match.supported[0]
    if block_message := _direct_domain_capability_block_message(ctx.state, supported.domain):
        return {
            **ctx.gate_updates,
            "capability_boundary": None,
            "direct_path_triggered": True,
            "final_response": f"{notice}\n\n{block_message}",
            "semantic_path_shape": "mixed_capability_supported_policy_blocked",
            **_route_observability_updates(
                owner="guardrail",
                decision="mixed_supported_unsupported_policy_blocked",
                target_domain=supported.domain,
                mode="new",
            ),
        }

    supported_state = _temporary_state_with_message(ctx.state, supported.text)
    task_id, spec = _build_supported_task(supported_state, supported)
    task_updates: dict[str, Any] = {}
    if supported.domain == "transfer":
        await ctx.ensure_query_session()
        if (
            ctx.redis_client
            and isinstance(ctx.query_session_snapshot, dict)
            and ctx.query_session_snapshot.get("session_active")
        ):
            await clear_query_session(ctx.redis_client, ctx.state.phone_number)
            task_updates.update(
                _build_query_session_exit_updates(
                    ctx.state,
                    query_session_snapshot=ctx.query_session_snapshot,
                )
            )
    if supported.domain == "transfer" and supported.heuristic_name == "recipient_bank_details_only":
        spec.payload["amount_suggestion_disabled"] = True

    logger.info(
        "gate_mixed_capability_supported_direct",
        supported_domain=supported.domain,
        unsupported=[item.key for item in match.unsupported],
    )
    return {
        **ctx.gate_updates,
        **(ctx.summary_updates or {}),
        **task_updates,
        "capability_boundary": None,
        "policy_notice": notice,
        "tasks": {task_id: spec},
        "waves": [[task_id]],
        "current_wave_index": 0,
        "planner_output": None,
        "pending_interrupt": None,
        "direct_path_triggered": True,
        "semantic_path_shape": "mixed_capability_supported_direct",
        **_route_observability_updates(
            owner="guardrail",
            decision="mixed_supported_unsupported",
            target_domain=supported.domain,
            mode="new",
            route_source="mixed_capability_guard",
            heuristic_type="clause_splitter",
            heuristic_name=supported.heuristic_name,
        ),
    }
