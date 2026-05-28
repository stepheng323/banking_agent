# Orchestrator

Use these entrypoints first:

- `apps.chat.src.agent.orchestrator.OrchestratorAgent`: runtime agent used by workers and scripts.
- `apps.chat.src.agent.orchestrator.graph.build_orchestrator_graph`: LangGraph construction for graph-level tests and handlers.

Navigation:

- `agent.py`: runtime orchestration, media preprocessing, context persistence, and graph handler setup.
- `graph/`: graph construction, invocation handling, progress delivery, locking, and route metrics.
- `workflows/`: graph-callable workflow entrypoints plus phase-specific implementation modules.
- `task_handlers/`: task-family handlers used by the execution workflow.
- `guardrails/`: deterministic cross-workflow policy and shortcut helpers.
- `context/`: context frame models, surface adapters, and short-term referent memory.
- `models/`, `presentation/`, `services/`, `utils/`: shared orchestrator support code.

Prefer public entrypoints at package boundaries. Import concrete workflow modules only when testing or changing workflow internals.
