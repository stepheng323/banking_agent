"""Architecture guards for the typed execution core."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPH_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "graph"
ORCHESTRATOR_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator"
WORKFLOWS_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows"
EXECUTION_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "execution"
TASK_HANDLERS_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "task_handlers"
INTERRUPT_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "interrupt"
GATE_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "gate"
PLANNER_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "planner"
DELETED_PLANNER_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "planning"
DELETED_CONTEXT_SERVICE_MODULE = ORCHESTRATOR_ROOT / "services" / "context_manager.py"
DELETED_ORCHESTRATOR_SERVICE_MODULES = (
    ORCHESTRATOR_ROOT / "services" / "__init__.py",
    ORCHESTRATOR_ROOT / "services" / "media_service.py",
    ORCHESTRATOR_ROOT / "services" / "media_text.py",
    ORCHESTRATOR_ROOT / "services" / "meta_reply.py",
)
DELETED_TASK_QUEUE_ROOT = ORCHESTRATOR_ROOT / "task_queue"
DELETED_PLANNER_CONTEXT_FLAT_MODULES = tuple(
    PLANNER_ROOT / "context" / filename
    for filename in (
        "context_flow.py",
        "context_flow_followup.py",
        "context_flow_hinting.py",
        "context_flow_mode_decisions.py",
        "context_flow_sections.py",
        "context_flow_state.py",
        "context_flow_types.py",
        "context_frame_account_status.py",
        "context_frame_data_plans.py",
        "context_frame_decisions.py",
        "context_frame_detail_blocks.py",
        "context_frame_detail_fields.py",
        "context_frame_detail_responses.py",
        "context_frame_filtering.py",
        "context_frame_followup_context_builder.py",
        "context_frame_followup_focus.py",
        "context_frame_followup_response_builder.py",
        "context_frame_followup_selection.py",
        "context_frame_followup_surface_engine.py",
        "context_frame_followup_types.py",
        "context_frame_ranking.py",
        "context_frame_replay.py",
        "context_frame_replay_accounts.py",
        "context_frame_replay_amounts.py",
        "context_frame_replay_modifier_core.py",
        "context_frame_replay_modifier_text.py",
        "context_frame_replay_narration.py",
        "context_frame_replay_payload_base.py",
        "context_frame_replay_payload_source.py",
        "context_frame_replay_payload_transactions.py",
        "context_frame_replay_payload_values.py",
        "context_frame_replay_targets.py",
        "context_frame_replay_tasks.py",
        "context_frame_response_explain.py",
        "context_frame_response_selection.py",
        "context_frame_schedule.py",
        "context_frame_search.py",
        "context_frame_semantic_response.py",
        "context_frame_state_view.py",
        "context_frame_text.py",
        "context_query_session.py",
        "context_read_account.py",
        "context_read_availability.py",
        "context_read_constants.py",
        "context_read_fallback.py",
        "context_read_focus.py",
        "context_read_frames.py",
        "context_rendering_active.py",
        "context_rendering_core.py",
        "context_rendering_router.py",
        "context_rendering_user.py",
        "context_summary.py",
        "context_summary_active_flow.py",
        "context_summary_focus.py",
        "context_summary_payload.py",
        "context_summary_state.py",
        "context_types.py",
    )
)
DELETED_PENDING_ACTION_FLAT_MODULES = tuple(
    INTERRUPT_ROOT / "pending_action" / filename
    for filename in (
        "pending_action_account_context.py",
        "pending_action_add_operation.py",
        "pending_action_amount_patches.py",
        "pending_action_confirmation_flow.py",
        "pending_action_data_plan_patches.py",
        "pending_action_edit_context.py",
        "pending_action_edit_engine.py",
        "pending_action_edit_scope.py",
        "pending_action_edit_types.py",
        "pending_action_field_operation.py",
        "pending_action_funding_patches.py",
        "pending_action_mobile_patches.py",
        "pending_action_payload_account_switch.py",
        "pending_action_payload_fields.py",
        "pending_action_payload_overrides.py",
        "pending_action_payload_patch_router.py",
        "pending_action_route_operations.py",
        "pending_action_semantic.py",
        "pending_action_source_account_patches.py",
        "pending_action_source_account_resolution.py",
        "pending_action_targets.py",
        "pending_action_transfer_patches.py",
    )
)
LIFECYCLE_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "lifecycle"
WORKFLOW_RUNTIME_CONFIG_MODULE = WORKFLOWS_ROOT / "runtime_config.py"
INTERRUPT_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "context.py",
    INTERRUPT_ROOT / "node.py",
    INTERRUPT_ROOT / "runtime.py",
)
INTERRUPT_LOCALE_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "auth" / "auth_flow.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_updates.py",
    INTERRUPT_ROOT / "deterministic" / "runner_deterministic_confirmation.py",
    INTERRUPT_ROOT / "expiry" / "expiry_stale_session.py",
    INTERRUPT_ROOT / "expiry" / "expiry_updates.py",
    INTERRUPT_ROOT / "input" / "input_reprompt.py",
    INTERRUPT_ROOT / "router" / "router_core.py",
    INTERRUPT_ROOT / "router" / "runner_routing.py",
)
INTERRUPT_REPROMPT_STATUS_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "input" / "input_continue.py",
    INTERRUPT_ROOT / "input" / "input_selection_route.py",
    INTERRUPT_ROOT / "input" / "input_slot_route.py",
    INTERRUPT_ROOT / "reprompt" / "reprompt_auth.py",
    INTERRUPT_ROOT / "reprompt" / "reprompt_confirmation.py",
    INTERRUPT_ROOT / "reprompt" / "reprompt_flow.py",
    INTERRUPT_ROOT / "reprompt" / "reprompt_input.py",
    INTERRUPT_ROOT / "status" / "status_query_flow.py",
    INTERRUPT_ROOT / "status" / "status_query_text.py",
)
INTERRUPT_PENDING_ACTION_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "pending_action" / "context" / "pending_action_account_context.py",
    INTERRUPT_ROOT / "pending_action" / "flow" / "pending_action_confirmation_flow.py",
    INTERRUPT_ROOT / "pending_action" / "engine" / "pending_action_edit_context.py",
    INTERRUPT_ROOT / "pending_action" / "engine" / "pending_action_edit_scope.py",
    INTERRUPT_ROOT / "pending_action" / "payloads" / "pending_action_payload_overrides.py",
    INTERRUPT_ROOT / "pending_action" / "targets" / "pending_action_targets.py",
)
INTERRUPT_CONFIRMATION_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "auth" / "auth_resolve.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_edit_remove.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_edit_restore.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_edit_target_tasks.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_edit_targets.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_repeat.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_selection.py",
    INTERRUPT_ROOT / "confirmation" / "confirmation_updates.py",
)
INTERRUPT_SWITCHING_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "switching" / "switch_extract_context.py",
    INTERRUPT_ROOT / "switching" / "switch_session_stash.py",
    INTERRUPT_ROOT / "switching" / "switch_update_additive.py",
)
INTERRUPT_FINAL_STATE_VIEW_MODULES = (
    INTERRUPT_ROOT / "pending_action" / "engine" / "pending_action_edit_engine.py",
    INTERRUPT_ROOT / "pending_action" / "payloads" / "pending_action_payload_patch_router.py",
    INTERRUPT_ROOT / "router" / "context_router.py",
    INTERRUPT_ROOT / "router" / "context_router_payloads.py",
    INTERRUPT_ROOT / "router" / "router_callbacks.py",
    INTERRUPT_ROOT / "router" / "router_core.py",
    INTERRUPT_ROOT / "router" / "router_semantic.py",
    INTERRUPT_ROOT / "schedule_read.py",
    INTERRUPT_ROOT / "switching" / "switch_update_planner.py",
    INTERRUPT_ROOT / "switching" / "switch_update_replacement.py",
)
GATE_STATE_VIEW_CONTRACT_MODULES = (
    GATE_ROOT / "core" / "context.py",
    GATE_ROOT / "utils" / "direct_tasks.py",
    GATE_ROOT / "state" / "interrupt_state.py",
    GATE_ROOT / "core" / "node.py",
    GATE_ROOT / "state" / "query_session_exit.py",
    GATE_ROOT / "core" / "runtime.py",
    GATE_ROOT / "utils" / "support_identity.py",
    GATE_ROOT / "utils" / "unsupported_capability_routing.py",
)
GATE_STATE_VIEW_STAGE_MODULES = (
    GATE_ROOT / "stages" / "capability_boundary_stages.py",
    GATE_ROOT / "stages" / "context_frame_stages.py",
    GATE_ROOT / "stages" / "contextual_followup_stages.py",
    GATE_ROOT / "stages" / "core_stages.py",
    GATE_ROOT / "stages" / "data_domain_stages.py",
    GATE_ROOT / "stages" / "direct_domain_stages.py",
    GATE_ROOT / "stages" / "domain_data_plan.py",
    GATE_ROOT / "stages" / "helpers.py",
    GATE_ROOT / "stages" / "meta_stages.py",
    GATE_ROOT / "stages" / "mixed_capability_stages.py",
    GATE_ROOT / "stages" / "query_transfer_stages.py",
    GATE_ROOT / "stages" / "schedule_read_stage.py",
    GATE_ROOT / "stages" / "semantic_routing" / "classifier.py",
    GATE_ROOT / "stages" / "semantic_routing" / "control_handlers.py",
    GATE_ROOT / "stages" / "semantic_routing" / "direct_response.py",
    GATE_ROOT / "stages" / "semantic_routing" / "domain_dispatch.py",
    GATE_ROOT / "stages" / "semantic_routing" / "pipeline.py",
    GATE_ROOT / "stages" / "session_action_stages.py",
    GATE_ROOT / "stages" / "stale_context_arbitration.py",
    GATE_ROOT / "stages" / "support_receipt_stages.py",
)
LIFECYCLE_STATE_VIEW_MODULES = (
    LIFECYCLE_ROOT / "finalize.py",
    LIFECYCLE_ROOT / "finalize_completed.py",
    LIFECYCLE_ROOT / "ingest.py",
    LIFECYCLE_ROOT / "reducer.py",
    LIFECYCLE_ROOT / "runtime.py",
)
PLANNER_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "execution_cleanup.py",
    PLANNER_ROOT / "execution_flow.py",
    PLANNER_ROOT / "execution_locale.py",
    PLANNER_ROOT / "node.py",
    PLANNER_ROOT / "node_task_response.py",
    PLANNER_ROOT / "runtime.py",
)
PLANNER_CONTEXT_FLOW_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "context" / "flow" / "context_flow.py",
    PLANNER_ROOT / "context" / "flow" / "context_flow_mode_decisions.py",
    PLANNER_ROOT / "context" / "flow" / "context_flow_state.py",
    PLANNER_ROOT / "context" / "query_session" / "context_query_session.py",
    PLANNER_ROOT / "context" / "read" / "context_read_focus.py",
    PLANNER_ROOT / "context" / "summary" / "context_summary_focus.py",
)
PLANNER_CONTEXT_SUMMARY_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "context" / "summary" / "context_summary.py",
    PLANNER_ROOT / "context" / "summary" / "context_summary_active_flow.py",
    PLANNER_ROOT / "context" / "summary" / "context_summary_state.py",
)
PLANNER_CONTEXT_READ_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "context" / "read" / "context_read_account.py",
    PLANNER_ROOT / "context" / "read" / "context_read_availability.py",
    PLANNER_ROOT / "context" / "read" / "context_read_frames.py",
    PLANNER_ROOT / "execution_context_read.py",
    PLANNER_ROOT / "execution_flow.py",
)
PLANNER_CONTEXT_FRAME_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "context" / "flow" / "context_flow_followup.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_followup_context_builder.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_followup_focus.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_followup_response_builder.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_followup_selection.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_followup_surface_engine.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_followup_types.py",
    PLANNER_ROOT / "context" / "replay" / "context_frame_replay.py",
    PLANNER_ROOT / "context" / "replay" / "context_frame_replay_accounts.py",
    PLANNER_ROOT / "context" / "replay" / "context_frame_replay_payload_source.py",
    PLANNER_ROOT / "context" / "replay" / "context_frame_replay_targets.py",
    PLANNER_ROOT / "context" / "replay" / "context_frame_replay_tasks.py",
    PLANNER_ROOT / "context" / "frames" / "context_frame_schedule.py",
)
PLANNER_QUOTED_REPLAY_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "quoted_replay" / "quoted_flow.py",
    PLANNER_ROOT / "quoted_replay" / "quoted_replay_context.py",
    PLANNER_ROOT / "quoted_replay" / "quoted_replay_modifiers.py",
    PLANNER_ROOT / "quoted_replay" / "quoted_replay_payload_updates.py",
)
PLANNER_RESPONSE_STATE_VIEW_MODULES = (
    PLANNER_ROOT / "policy" / "policy_locale.py",
    PLANNER_ROOT / "postprocess" / "postprocess_flow.py",
    PLANNER_ROOT / "response" / "non_task_response.py",
    PLANNER_ROOT / "response" / "response_flow_cancellation.py",
    PLANNER_ROOT / "response" / "response_flow_common.py",
    PLANNER_ROOT / "response" / "response_flow_conversational.py",
    PLANNER_ROOT / "response" / "response_flow_conversational_casual.py",
    PLANNER_ROOT / "response" / "response_flow_conversational_direct.py",
    PLANNER_ROOT / "response" / "response_flow_conversational_missing.py",
    PLANNER_ROOT / "response" / "response_flow_conversational_out_of_scope.py",
    PLANNER_ROOT / "response" / "response_flow_conversational_special.py",
    PLANNER_ROOT / "response" / "response_flow_conversational_standard.py",
    PLANNER_ROOT / "response" / "response_flow_logging.py",
)
EXECUTION_INTERRUPT_PATCH_MODULES = (
    EXECUTION_ROOT / "auth_gate_updates.py",
    EXECUTION_ROOT / "confirmation" / "confirmation_gate_updates.py",
    EXECUTION_ROOT / "prompts" / "input_prompts.py",
    EXECUTION_ROOT / "prompts" / "input_prompts_focused.py",
    EXECUTION_ROOT / "funding" / "batch_funding_coordination.py",
)
EXECUTION_TASK_MUTATION_CONTRACT_MODULES = (
    EXECUTION_ROOT / "async_grouping.py",
    EXECUTION_ROOT / "executors" / "account_beneficiary.py",
    EXECUTION_ROOT / "executors" / "purchase.py",
    EXECUTION_ROOT / "executors" / "query.py",
    EXECUTION_ROOT / "executors" / "session.py",
    EXECUTION_ROOT / "executors" / "support.py",
    EXECUTION_ROOT / "executors" / "transfer.py",
    EXECUTION_ROOT / "prompts" / "input_prompt_batch_source.py",
    EXECUTION_ROOT / "prompts" / "input_prompts_focused.py",
    EXECUTION_ROOT / "prompts" / "input_prompts_unified.py",
    EXECUTION_ROOT / "result_reducer.py",
    EXECUTION_ROOT / "source_selection.py",
    EXECUTION_ROOT / "task_input.py",
    EXECUTION_ROOT / "wave" / "runner_task_guards.py",
    EXECUTION_ROOT / "wave" / "wave_state.py",
)
EXECUTION_ACCUMULATOR_MODULE = EXECUTION_ROOT / "accumulator.py"
EXECUTION_RESULT_PATCH_MODULE = EXECUTION_ROOT / "result_patch.py"
EXECUTION_SESSION_STACK_MODULE = EXECUTION_ROOT / "session_stack.py"
EXECUTION_CONTROL_STATE_MODULE = EXECUTION_ROOT / "control_state.py"
EXECUTION_CONTEXT_SURFACE_MODULE = EXECUTION_ROOT / "context_surface.py"
EXECUTION_LAST_INTERRUPT_MODULE = EXECUTION_ROOT / "last_interrupt.py"
EXECUTION_LOADED_CONTEXT_MODULE = EXECUTION_ROOT / "loaded_context.py"
EXECUTION_TASK_ACCESS_MODULE = EXECUTION_ROOT / "task_access.py"
EXECUTION_TURN_METADATA_MODULE = EXECUTION_ROOT / "turn_metadata.py"
EXECUTION_WAVE_STATE_MODULE = EXECUTION_ROOT / "wave" / "wave_state.py"

DELETED_EXECUTION_MODULE_PATHS = (
    TASK_HANDLERS_ROOT,
    ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "task_handlers" / "runtime.py",
    EXECUTION_ROOT / "runtime.py",
    EXECUTION_ROOT / "wave" / "runner_task_handlers.py",
)

FORBIDDEN_EXECUTION_TEXT = (
    "apps.chat.src.agent.orchestrator.task_handlers",
    "apps.chat.src.agent.orchestrator.task_handlers.runtime",
    "apps.chat.src.agent.orchestrator.workflows.execution.runtime",
    "apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_task_handlers",
    "ExecutionServices",
    "ExecutionResultPatch = dict",
    "FunctionTaskExecutor",
    "TaskHandler =",
    "get_task_executor(",
    "ctx.accumulator.updates",
    "agg.updates",
    "runtime.accumulator.updates",
    "_HANDLERS",
    'config["configurable"].get("services"',
    "config_value(",
)

FORBIDDEN_INTERRUPT_TEXT = (
    "services: dict[str, Any]",
    'config["configurable"].get("services"',
)

FORBIDDEN_GATE_TEXT = (
    'config["configurable"]',
    'config.get("configurable"',
)

FORBIDDEN_PLANNER_TEXT = (
    'config["configurable"]',
    'config.get("configurable"',
)

FORBIDDEN_DELETED_PLANNER_TEXT = (
    "apps.chat.src.agent.orchestrator.planning",
    "apps/chat/src/agent/orchestrator/planning",
)

FORBIDDEN_DELETED_CONTEXT_SERVICE_TEXT = (
    "apps.chat.src.agent.orchestrator.services.context_manager",
    "apps/chat/src/agent/orchestrator/services/context_manager.py",
    "OrchestratorContextManager",
)

FORBIDDEN_DELETED_ORCHESTRATOR_SERVICES_TEXT = (
    "apps.chat.src.agent.orchestrator.services.media_service",
    "apps.chat.src.agent.orchestrator.services.media_text",
    "apps.chat.src.agent.orchestrator.services.meta_reply",
    "apps/chat/src/agent/orchestrator/services/media_service.py",
    "apps/chat/src/agent/orchestrator/services/media_text.py",
    "apps/chat/src/agent/orchestrator/services/meta_reply.py",
)

FORBIDDEN_DELETED_TASK_QUEUE_TEXT = (
    "apps.chat.src.agent.orchestrator.task_queue",
    "apps/chat/src/agent/orchestrator/task_queue",
    "TaskQueueService",
    "task_queue_service",
)

FORBIDDEN_DELETED_PLANNER_CONTEXT_TEXT = tuple(
    f"apps.chat.src.agent.orchestrator.workflows.planner.context.{path.stem}"
    for path in DELETED_PLANNER_CONTEXT_FLAT_MODULES
) + tuple(str(path.relative_to(ROOT)) for path in DELETED_PLANNER_CONTEXT_FLAT_MODULES)

FORBIDDEN_DELETED_PENDING_ACTION_TEXT = tuple(
    f"apps.chat.src.agent.orchestrator.workflows.interrupt.pending_action.{path.stem}"
    for path in DELETED_PENDING_ACTION_FLAT_MODULES
)

FORBIDDEN_LIFECYCLE_TEXT = (
    'config["configurable"]',
    'config.get("configurable"',
)

FORBIDDEN_GRAPH_CONFIG_TEXT = (
    'config["configurable"]',
    'config.get("configurable"',
)

MOVED_EXECUTION_RUNTIME_SYMBOLS = {
    "ExecutionAccumulator",
    "ExecutionDependencies",
    "ExecutionTurnContext",
}

ACCUMULATOR_MODULE = "apps.chat.src.agent.orchestrator.workflows.execution.accumulator"
FORBIDDEN_ACCUMULATOR_METHODS = {"set_update", "get_update", "has_update"}
FORBIDDEN_ACCUMULATOR_LIST_APPENDS = {
    "feedback_messages",
    "needs_auth_tasks",
    "needs_confirm_tasks",
    "source_bank_hints",
}
FORBIDDEN_ACCUMULATOR_MAPPING_MUTATION_METHODS = {"clear", "pop", "setdefault", "update"}
FORBIDDEN_ACCUMULATOR_MAPPING_MUTATIONS = {
    "details_by_task",
    "missing_fields_by_task",
    "prompts_by_task",
}
FORBIDDEN_ACCUMULATOR_PUBLIC_FIELDS = {
    "details_by_task",
    "feedback_messages",
    "missing_fields_by_task",
    "needs_auth_tasks",
    "needs_confirm_tasks",
    "prompts_by_task",
    "source_bank_hints",
}
FORBIDDEN_RESULT_PATCH_METHODS = {"set_update", "get_update", "has_update"}


def _python_sources() -> list[Path]:
    roots = (ROOT / "apps", ROOT / "tests")
    files: list[Path] = []
    for root in roots:
        files.extend(root.rglob("*.py"))
    return sorted(files)


def _python_and_script_sources() -> list[Path]:
    roots = (ROOT / "apps", ROOT / "tests", ROOT / "scripts")
    files: list[Path] = []
    for root in roots:
        files.extend(root.rglob("*.py"))
    return sorted(files)


def _path_has_python_sources(path: Path) -> bool:
    if path.is_file():
        return path.suffix == ".py"
    if path.is_dir():
        return any(child.is_file() and child.suffix == ".py" for child in path.rglob("*.py"))
    return False


def test_deleted_execution_compatibility_modules_do_not_exist() -> None:
    existing = [path.relative_to(ROOT) for path in DELETED_EXECUTION_MODULE_PATHS if _path_has_python_sources(path)]

    assert existing == []


def test_typed_execution_core_does_not_reference_deleted_paths_or_raw_handler_map() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_EXECUTION_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_execution_context_and_accumulator_use_canonical_imports() -> None:
    violations: list[str] = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "apps.chat.src.agent.orchestrator.workflows.execution.runtime":
                continue
            moved_symbols = [alias.name for alias in node.names if alias.name in MOVED_EXECUTION_RUNTIME_SYMBOLS]
            if moved_symbols:
                symbol_list = ", ".join(sorted(moved_symbols))
                violations.append(f"{path.relative_to(ROOT)} imports moved execution symbols: {symbol_list}")

    assert violations == []


def test_execution_result_patch_lives_in_canonical_module() -> None:
    violations: list[str] = []
    accumulator_text = (EXECUTION_ROOT / "accumulator.py").read_text(encoding="utf-8")
    result_patch_text = (EXECUTION_ROOT / "result_patch.py").read_text(encoding="utf-8")
    if "class ExecutionResultPatch" in accumulator_text:
        violations.append("accumulator.py defines ExecutionResultPatch")
    if "class ExecutionResultPatch" not in result_patch_text:
        violations.append("result_patch.py does not define ExecutionResultPatch")
    for method_name in FORBIDDEN_RESULT_PATCH_METHODS:
        if f"def {method_name}(" in result_patch_text:
            violations.append(f"result_patch.py exposes generic {method_name}()")

    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != ACCUMULATOR_MODULE:
                continue
            if any(alias.name == "ExecutionResultPatch" for alias in node.names):
                violations.append(f"{path.relative_to(ROOT)} imports ExecutionResultPatch from accumulator")

    assert violations == []


def test_execution_result_patch_callers_use_typed_methods() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in FORBIDDEN_RESULT_PATCH_METHODS:
                continue
            target = node.func.value
            if isinstance(target, ast.Name) and target.id == "patch":
                violations.append(f"{path.relative_to(ROOT)} calls patch.{node.func.attr}()")

    assert violations == []


def test_execution_accumulator_callers_use_typed_methods() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in FORBIDDEN_ACCUMULATOR_METHODS:
                continue
            target = node.func.value
            if isinstance(target, ast.Attribute) and target.attr == "accumulator":
                violations.append(f"{path.relative_to(ROOT)} calls {ast.unparse(target)}.{node.func.attr}()")
            elif isinstance(target, ast.Name) and target.id == "accumulator":
                violations.append(f"{path.relative_to(ROOT)} calls accumulator.{node.func.attr}()")

    assert violations == []


def test_execution_accumulator_result_patch_is_private() -> None:
    violations: list[str] = []
    accumulator_text = (EXECUTION_ROOT / "accumulator.py").read_text(encoding="utf-8")
    if "self.result_patch" in accumulator_text:
        violations.append("ExecutionAccumulator exposes public result_patch attribute")
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "result_patch":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_accumulator_state_storage_is_private() -> None:
    violations: list[str] = []
    accumulator_text = (EXECUTION_ROOT / "accumulator.py").read_text(encoding="utf-8")
    for field_name in FORBIDDEN_ACCUMULATOR_PUBLIC_FIELDS | {"prompts"}:
        if f"self.{field_name}" in accumulator_text:
            violations.append(f"ExecutionAccumulator exposes public {field_name} attribute")

    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ACCUMULATOR_PUBLIC_FIELDS:
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "prompts"
                and (
                    (isinstance(node.value, ast.Name) and node.value.id in {"agg", "accumulator"})
                    or (isinstance(node.value, ast.Attribute) and node.value.attr == "accumulator")
                )
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_accumulator_callers_do_not_mutate_reducer_lists_directly() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "append":
                continue
            target = node.func.value
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr in FORBIDDEN_ACCUMULATOR_LIST_APPENDS:
                violations.append(f"{path.relative_to(ROOT)} calls {ast.unparse(target)}.append()")

    assert violations == []


def test_execution_accumulator_callers_do_not_mutate_prompt_maps_directly() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = node.func.value
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr in FORBIDDEN_ACCUMULATOR_MAPPING_MUTATIONS
                    and node.func.attr in FORBIDDEN_ACCUMULATOR_MAPPING_MUTATION_METHODS
                ):
                    violations.append(f"{path.relative_to(ROOT)} calls {ast.unparse(target)}.{node.func.attr}()")
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr in FORBIDDEN_ACCUMULATOR_MAPPING_MUTATIONS
                    ):
                        violations.append(f"{path.relative_to(ROOT)} deletes {ast.unparse(target)}")
            elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr in FORBIDDEN_ACCUMULATOR_MAPPING_MUTATIONS
                    ):
                        violations.append(f"{path.relative_to(ROOT)} assigns {ast.unparse(target)}")

    assert violations == []


def test_execution_result_patch_is_constructed_only_by_accumulator() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name in {"accumulator.py", "result_patch.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ExecutionResultPatch"
            ):
                violations.append(f"{path.relative_to(ROOT)} constructs ExecutionResultPatch")

    assert violations == []


def test_execution_pending_interrupt_is_constructed_only_by_accumulator() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.name == "accumulator.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "PendingInterrupt":
                violations.append(f"{path.relative_to(ROOT)} constructs PendingInterrupt")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"set_pending_interrupt", "set_interrupt_outbox"}
            ):
                violations.append(f"{path.relative_to(ROOT)} calls generic {node.func.attr}()")

    assert violations == []


def test_execution_task_mutations_use_typed_helpers() -> None:
    violations: list[str] = []
    for path in EXECUTION_TASK_MUTATION_CONTRACT_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr in {"stage", "type"}:
                        violations.append(f"{path.relative_to(ROOT)} assigns {ast.unparse(target)}")
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "payload"
                    ):
                        violations.append(f"{path.relative_to(ROOT)} assigns {ast.unparse(target)}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = node.func.value
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "payload"
                    and node.func.attr in {"clear", "pop", "setdefault", "update"}
                ):
                    violations.append(f"{path.relative_to(ROOT)} calls {ast.unparse(target)}.{node.func.attr}()")

    assert violations == []


def test_execution_session_stack_mutations_use_typed_helpers() -> None:
    violations: list[str] = []
    allowed_modules = {
        EXECUTION_ACCUMULATOR_MODULE.resolve(),
        EXECUTION_RESULT_PATCH_MODULE.resolve(),
        EXECUTION_SESSION_STACK_MODULE.resolve(),
    }
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() in allowed_modules:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "session_stack":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "set_session_stack":
                    violations.append(f"{path.relative_to(ROOT)} calls {ast.unparse(node.func)}()")
            if isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "state"
                        and isinstance(target.value, ast.Subscript)
                    ):
                        violations.append(f"{path.relative_to(ROOT)} assigns {ast.unparse(target)}")

    assert violations == []


def test_execution_wave_task_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_TASK_ACCESS_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "tasks":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_loaded_context_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_LOADED_CONTEXT_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "loaded_context":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_last_interrupt_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_LAST_INTERRUPT_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "last_interrupt":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_context_surface_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    guarded_attrs = {"context_frames", "referent_memory"}
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_CONTEXT_SURFACE_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in guarded_attrs:
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_wave_position_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    guarded_attrs = {"current_wave_index", "waves"}
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_WAVE_STATE_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id == "ctx"
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_turn_metadata_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "channel",
        "channel_identity",
        "last_message_id",
        "last_message_text",
        "phone_number",
        "pin_verified",
        "quoted_message_id",
        "stashed_query_session",
        "stashed_sessions",
    }
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_TURN_METADATA_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id == "ctx"
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_control_state_reads_use_typed_helpers() -> None:
    violations: list[str] = []
    guarded_attrs = {"pending_interrupt", "policy_notice"}
    for path in sorted(EXECUTION_ROOT.rglob("*.py")):
        if path.resolve() == EXECUTION_CONTROL_STATE_MODULE.resolve():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id == "ctx"
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_execution_interrupt_builders_use_accumulator_contract() -> None:
    violations: list[str] = []
    for path in EXECUTION_INTERRUPT_PATCH_MODULES:
        text = path.read_text(encoding="utf-8")
        if "ExecutionResultPatch" in text:
            violations.append(f"{path.relative_to(ROOT)} imports ExecutionResultPatch")
        if "_interrupt_outbox" not in text:
            violations.append(f"{path.relative_to(ROOT)} does not use accumulator interrupt outbox contract")
        if "updates = {" in text or "updates: dict[str, Any] = {" in text or "\n    return {" in text:
            violations.append(f"{path.relative_to(ROOT)} assembles raw update dict")

    assert violations == []


def test_typed_interrupt_core_does_not_forward_raw_service_mappings() -> None:
    violations: list[str] = []
    for path in sorted(INTERRUPT_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_INTERRUPT_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_interrupt_foundational_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "last_message_text",
        "loaded_context",
        "pending_interrupt",
        "session_stack",
        "tasks",
    }
    for path in INTERRUPT_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_interrupt_locale_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    for path in INTERRUPT_LOCALE_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "loaded_context":
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_interrupt_reprompt_status_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {"loaded_context", "tasks"}
    for path in INTERRUPT_REPROMPT_STATUS_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_interrupt_pending_action_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {"loaded_context", "removed_confirmation_tasks", "tasks"}
    for path in INTERRUPT_PENDING_ACTION_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_interrupt_confirmation_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "current_wave_index",
        "last_message_text",
        "pin_verified",
        "removed_confirmation_tasks",
        "task_results",
        "tasks",
        "waves",
    }
    for path in INTERRUPT_CONFIRMATION_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_interrupt_switching_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "current_wave_index",
        "loaded_context",
        "phone_number",
        "session_stack",
        "stashed_sessions",
        "task_results",
        "tasks",
        "waves",
    }
    for path in INTERRUPT_SWITCHING_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_remaining_interrupt_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "context_frames",
        "last_callback",
        "phone_number",
        "pin_verified",
        "preplanner_expected_transaction_executors",
        "referent_memory",
        "session_stack",
        "stashed_query_session",
        "tasks",
    }
    for path in INTERRUPT_FINAL_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_typed_gate_core_reads_configurable_only_in_runtime_builder() -> None:
    violations: list[str] = []
    for path in sorted(GATE_ROOT.rglob("*.py")):
        if path.name == "runtime.py":
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_GATE_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_gate_registry_does_not_retain_legacy_symbols() -> None:
    violations: list[str] = []
    forbidden_text = ("GATE_LEGACY_HANDLER_SPECS", "_GATE_STAGES")
    for path in sorted(GATE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in forbidden_text:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_workflow_configurable_reads_live_only_in_runtime_config_accessor() -> None:
    violations: list[str] = []
    forbidden_text = (
        'config["configurable"]',
        'config.get("configurable"',
    )
    for path in sorted(WORKFLOWS_ROOT.rglob("*.py")):
        if path.resolve() == WORKFLOW_RUNTIME_CONFIG_MODULE.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in forbidden_text:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_gate_foundational_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "active_domain",
        "capability_boundary",
        "channel_identity",
        "context_frames",
        "final_response",
        "has_quote",
        "last_callback",
        "last_message_text",
        "loaded_context",
        "pending_interrupt",
        "phone_number",
        "pin_verified",
        "preplanner_expected_transaction_executors",
        "session_stack",
        "stashed_sessions",
        "stashed_query_session",
        "task_results",
        "tasks",
        "user_id",
        "waves",
    }
    for path in GATE_STATE_VIEW_CONTRACT_MODULES + GATE_STATE_VIEW_STAGE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_gate_modules_do_not_clone_raw_orchestrator_state() -> None:
    violations: list[str] = []
    for path in GATE_STATE_VIEW_CONTRACT_MODULES + GATE_STATE_VIEW_STAGE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "model_copy":
                continue
            target = node.func.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} clones raw {ast.unparse(target)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "self", "state_view"}
            ):
                violations.append(f"{path.relative_to(ROOT)} clones raw {ast.unparse(target)}")

    assert violations == []


def test_typed_planner_core_reads_configurable_only_in_runtime_builder() -> None:
    violations: list[str] = []
    for path in sorted(PLANNER_ROOT.rglob("*.py")):
        if path.name == "runtime.py":
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_PLANNER_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_top_level_planner_package_was_moved_into_workflow_planner_core() -> None:
    assert not _path_has_python_sources(DELETED_PLANNER_ROOT)


def test_old_top_level_planner_package_is_not_referenced() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_and_script_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DELETED_PLANNER_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_context_frame_manager_lives_under_context_package() -> None:
    assert not _path_has_python_sources(DELETED_CONTEXT_SERVICE_MODULE)


def test_old_context_frame_service_module_is_not_referenced() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_and_script_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DELETED_CONTEXT_SERVICE_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_orchestrator_media_and_meta_services_are_not_in_generic_services_package() -> None:
    existing = [
        path.relative_to(ROOT) for path in DELETED_ORCHESTRATOR_SERVICE_MODULES if _path_has_python_sources(path)
    ]

    assert existing == []


def test_old_orchestrator_media_and_meta_service_paths_are_not_referenced() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_and_script_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DELETED_ORCHESTRATOR_SERVICES_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_task_state_service_replaces_task_queue_package() -> None:
    assert not _path_has_python_sources(DELETED_TASK_QUEUE_ROOT)


def test_old_task_queue_package_is_not_referenced() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_and_script_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DELETED_TASK_QUEUE_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_planner_context_modules_are_grouped_not_flat() -> None:
    existing = [
        path.relative_to(ROOT) for path in DELETED_PLANNER_CONTEXT_FLAT_MODULES if _path_has_python_sources(path)
    ]

    assert existing == []


def test_old_flat_planner_context_modules_are_not_referenced() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_and_script_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DELETED_PLANNER_CONTEXT_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_pending_action_modules_are_grouped_not_flat() -> None:
    existing = [
        path.relative_to(ROOT) for path in DELETED_PENDING_ACTION_FLAT_MODULES if _path_has_python_sources(path)
    ]

    assert existing == []


def test_old_flat_pending_action_modules_are_not_referenced() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_and_script_sources():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_DELETED_PENDING_ACTION_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_planner_entry_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "last_message_id",
        "last_message_text",
        "loaded_context",
        "pending_interrupt",
        "phone_number",
        "session_stack",
        "stashed_query_session",
        "waves",
    }
    for path in PLANNER_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_planner_context_flow_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "context_frames",
        "current_wave_index",
        "direct_path_triggered",
        "has_quote",
        "pending_interrupt",
        "phone_number",
        "planner_output",
        "preplanner_expected_transaction_executors",
        "quoted_message_id",
        "routing_decision",
        "routing_owner",
        "routing_target_domain",
        "session_stack",
        "stashed_query_session",
        "tasks",
        "waves",
    }
    for path in PLANNER_CONTEXT_FLOW_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_planner_context_summary_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "active_domain",
        "current_wave_index",
        "loaded_context",
        "pending_interrupt",
        "phone_number",
        "session_stack",
        "tasks",
        "turn_context_summary",
        "waves",
    }
    for path in PLANNER_CONTEXT_SUMMARY_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_planner_context_read_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "context_frames",
        "last_message_id",
        "loaded_context",
        "pending_interrupt",
        "referent_memory",
        "tasks",
    }
    for path in PLANNER_CONTEXT_READ_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_planner_context_frame_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "context_frames",
        "loaded_context",
        "phone_number",
        "tasks",
    }
    for path in PLANNER_CONTEXT_FRAME_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "request", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_planner_quoted_replay_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "has_quote",
        "loaded_context",
        "phone_number",
        "quoted_message_id",
        "tasks",
        "user_id",
    }
    for path in PLANNER_QUOTED_REPLAY_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "runtime", "request", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_planner_response_modules_use_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "current_wave_index",
        "last_message_text",
        "loaded_context",
        "pending_interrupt",
        "phone_number",
        "session_stack",
        "tasks",
        "waves",
    }
    for path in PLANNER_RESPONSE_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"ctx", "runtime", "request", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_typed_lifecycle_core_reads_configurable_only_in_runtime_builder() -> None:
    violations: list[str] = []
    for path in sorted(LIFECYCLE_ROOT.rglob("*.py")):
        if path.name == "runtime.py":
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_LIFECYCLE_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []


def test_lifecycle_core_uses_typed_state_view() -> None:
    violations: list[str] = []
    guarded_attrs = {
        "channel",
        "channel_identity",
        "context_frames",
        "current_wave_index",
        "last_activity_date",
        "last_callback",
        "last_message_id",
        "last_message_text",
        "loaded_context",
        "outbox",
        "pending_interrupt",
        "phone_number",
        "referent_memory",
        "stashed_sessions",
        "tasks",
        "waves",
    }
    for path in LIFECYCLE_STATE_VIEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in guarded_attrs:
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id == "state":
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "state"
                and isinstance(target.value, ast.Name)
                and target.value.id in {"runtime", "self"}
            ):
                violations.append(f"{path.relative_to(ROOT)} reaches into {ast.unparse(node)}")

    assert violations == []


def test_finalize_node_delegates_to_lifecycle_reducer() -> None:
    finalize_path = LIFECYCLE_ROOT / "finalize.py"
    tree = ast.parse(finalize_path.read_text(encoding="utf-8"))
    finalize_defs = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "finalize"]

    assert len(finalize_defs) == 1
    assert "reduce_finalize_runtime" in finalize_path.read_text(encoding="utf-8")

    non_docstring_body = [
        node
        for node in finalize_defs[0].body
        if not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        )
    ]
    assert len(non_docstring_body) <= 2


def test_graph_handler_does_not_reach_into_runnable_configurable() -> None:
    violations: list[str] = []
    for path in sorted(GRAPH_ROOT.rglob("*.py")):
        if path.name == "runtime.py":
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_GRAPH_CONFIG_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []
