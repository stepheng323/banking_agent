"""Architecture guards for the typed execution core."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPH_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "graph"
EXECUTION_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "execution"
TASK_HANDLERS_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "task_handlers"
INTERRUPT_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "interrupt"
GATE_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "gate"
PLANNER_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "planner"
LIFECYCLE_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "lifecycle"
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
EXECUTION_SESSION_STACK_CONTRACT_MODULES = (
    EXECUTION_ROOT / "executors" / "query.py",
    EXECUTION_ROOT / "executors" / "support.py",
    EXECUTION_ROOT / "executors" / "transfer.py",
)
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


def test_deleted_execution_compatibility_modules_do_not_exist() -> None:
    existing = [path.relative_to(ROOT) for path in DELETED_EXECUTION_MODULE_PATHS if path.exists()]

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
    for path in EXECUTION_SESSION_STACK_CONTRACT_MODULES:
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
