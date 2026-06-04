"""Architecture guards for the typed execution core."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPH_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "graph"
EXECUTION_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "execution"
INTERRUPT_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "interrupt"
GATE_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "gate"
PLANNER_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "planner"
LIFECYCLE_ROOT = ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "workflows" / "lifecycle"

DELETED_EXECUTION_MODULE_PATHS = (
    ROOT / "apps" / "chat" / "src" / "agent" / "orchestrator" / "task_handlers" / "runtime.py",
    EXECUTION_ROOT / "runtime.py",
    EXECUTION_ROOT / "wave" / "runner_task_handlers.py",
)

FORBIDDEN_EXECUTION_TEXT = (
    "apps.chat.src.agent.orchestrator.task_handlers.runtime",
    "apps.chat.src.agent.orchestrator.workflows.execution.runtime",
    "apps.chat.src.agent.orchestrator.workflows.execution.wave.runner_task_handlers",
    "ExecutionServices",
    "FunctionTaskExecutor",
    "TaskHandler =",
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
