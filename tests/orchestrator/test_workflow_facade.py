import apps.chat.src.agent.orchestrator.workflows as workflows
from apps.chat.src.agent.orchestrator.graph import build_orchestrator_graph
from apps.chat.src.agent.orchestrator.workflows.execution.node import advance_wave
from apps.chat.src.agent.orchestrator.workflows.gate.node import session_gate_direct_path
from apps.chat.src.agent.orchestrator.workflows.interrupt.node import handle_pending_interrupt
from apps.chat.src.agent.orchestrator.workflows.lifecycle.finalize import finalize
from apps.chat.src.agent.orchestrator.workflows.lifecycle.ingest import ingest_message
from apps.chat.src.agent.orchestrator.workflows.planner.node import plan_tasks


def test_workflows_facade_exports_public_graph_callables() -> None:
    assert workflows.advance_wave is advance_wave
    assert workflows.finalize is finalize
    assert workflows.handle_pending_interrupt is handle_pending_interrupt
    assert workflows.ingest_message is ingest_message
    assert workflows.plan_tasks is plan_tasks
    assert workflows.session_gate_direct_path is session_gate_direct_path


def test_orchestrator_graph_builds_from_workflows_facade() -> None:
    graph = build_orchestrator_graph()

    assert graph is not None
