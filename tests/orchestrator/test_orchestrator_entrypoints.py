import apps.chat.src.agent.orchestrator as orchestrator
import apps.chat.src.agent.orchestrator.graph as graph
from apps.chat.src.agent.orchestrator.agent import OrchestratorAgent
from apps.chat.src.agent.orchestrator.graph.builder import build_orchestrator_graph


def test_orchestrator_package_exports_runtime_entrypoints() -> None:
    assert orchestrator.OrchestratorAgent is OrchestratorAgent
    assert orchestrator.build_orchestrator_graph is build_orchestrator_graph


def test_graph_package_exports_builder_entrypoint() -> None:
    assert graph.build_orchestrator_graph is build_orchestrator_graph
