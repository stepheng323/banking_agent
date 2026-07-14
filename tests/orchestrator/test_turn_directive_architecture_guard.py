import ast
from pathlib import Path


def test_architecture_no_legacy_routing_keys() -> None:
    legacy_keys = {
        "routing_owner",
        "routing_decision",
        "routing_target_domain",
        "routing_mode",
        "route_source",
        "routing_heuristic_type",
        "routing_heuristic_name",
    }

    # Flat names survive only at log projections and the v1 checkpoint migration boundary.
    exclude_paths = {
        "apps/chat/src/agent/orchestrator/workflows/gate/core/trace.py",
        "apps/chat/src/agent/orchestrator/graph/turn_trace.py",
        "apps/chat/src/agent/orchestrator/graph/route_metrics.py",
        "apps/chat/src/agent/orchestrator/models/state.py",
        "apps/chat/src/agent/orchestrator/models/turn_directive.py",
    }

    workspace_root = Path(__file__).parent.parent.parent
    apps_dir = workspace_root / "apps" / "chat" / "src" / "agent" / "orchestrator"

    violations = []

    for py_file in apps_dir.rglob("*.py"):
        rel_path = str(py_file.relative_to(workspace_root))
        if rel_path in exclude_paths:
            continue

        content = py_file.read_text(encoding="utf-8")
        if any(key in content for key in legacy_keys):
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in legacy_keys:
                        violations.append(f"{rel_path}: string literal '{node.value}' (line {node.lineno})")
                elif isinstance(node, ast.Attribute) and node.attr in legacy_keys:
                    violations.append(f"{rel_path}: attribute '.{node.attr}' (line {node.lineno})")
                elif isinstance(node, ast.Name) and node.id in legacy_keys:
                    violations.append(f"{rel_path}: name '{node.id}' (line {node.lineno})")

    assert not violations, "Found legacy routing keys in code:\n" + "\n".join(violations)


def test_state_exposes_only_the_typed_route_contract() -> None:
    from apps.chat.src.agent.orchestrator.models.state import OrchestratorState

    assert "turn_directive" in OrchestratorState.model_fields
    assert not {
        "routing_owner",
        "routing_decision",
        "routing_target_domain",
        "routing_mode",
        "route_source",
        "routing_heuristic_type",
        "routing_heuristic_name",
    }.intersection(OrchestratorState.model_fields)


def test_graph_control_reads_only_the_directive_next_step() -> None:
    workspace_root = Path(__file__).parent.parent.parent
    builder_path = (
        workspace_root
        / "apps"
        / "chat"
        / "src"
        / "agent"
        / "orchestrator"
        / "graph"
        / "builder.py"
    )
    tree = ast.parse(builder_path.read_text(encoding="utf-8"))
    route_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_route_")
    }

    forbidden = {
        "direct_path_triggered",
        "final_response",
        "semantic_path_shape",
        "tasks",
        "waves",
        "pending_interrupt",
    }
    violations = []
    for function_name, function in route_functions.items():
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                violations.append(f"{function_name}: .{node.attr} (line {node.lineno})")
            if isinstance(node, ast.Constant) and node.value in forbidden:
                violations.append(f"{function_name}: {node.value!r} (line {node.lineno})")

    assert route_functions
    assert not violations, "Graph routing reads competing authority:\n" + "\n".join(violations)


def test_runtime_constructs_directives_only_through_the_factory() -> None:
    workspace_root = Path(__file__).parent.parent.parent
    orchestrator_dir = workspace_root / "apps" / "chat" / "src" / "agent" / "orchestrator"
    factory_path = orchestrator_dir / "models" / "turn_directive.py"
    violations = []

    for py_file in orchestrator_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            if isinstance(called, ast.Name) and called.id == "TurnDirective" and py_file != factory_path:
                rel_path = py_file.relative_to(workspace_root)
                violations.append(f"{rel_path}: raw TurnDirective construction (line {node.lineno})")

    assert not violations, "Raw directive construction bypasses the factory:\n" + "\n".join(violations)
