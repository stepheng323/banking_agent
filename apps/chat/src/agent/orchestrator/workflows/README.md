# Orchestrator Workflows

This package owns the runtime steps wired by [`graph/builder.py`](../graph/builder.py). Keep this tree organized by graph phase rather than by old node-package names.

## Top-Level Flow

```text
ingest -> gate -> plan -> advance -> finalize
           |        |
           |        +-> may end early with a final response
           +-> handle_interrupt when a pending interaction is active
```

## Directory Map

- `lifecycle/`: graph boundary work such as message ingest, final response assembly, resume prompts, and completed transaction frames.
- `gate/`: deterministic pre-planner routing, fast paths, support routing, locale state, and unsupported-capability routing.
- `interrupt/`: pending-interaction handling, including auth, confirmation edits, input resolution, schedule reads, pending-action edits, and switching.
- `planner/`: task-planner node flow, planner guardrails, context reads, quoted replay, policy checks, postprocessing, and response shaping.
- `execution/`: task wave advancement, source selection, funding, confirmation updates, execution prompts, and wave bookkeeping.

## Navigation Rules

- Graph construction imports public entrypoints from `workflows/__init__.py`.
- Workflow internals and tests should import concrete workflow modules directly.
- Put graph-callable node entrypoints in each phase's `node.py`.
- Put new behavior under the phase that owns when it runs in the graph.
- Put task-family execution helpers in `../task_handlers/`, not under this package.
- Avoid compatibility wrappers for old `nodes/` paths; update callers to the owning module instead.
