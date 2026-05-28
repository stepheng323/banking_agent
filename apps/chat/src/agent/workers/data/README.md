# Data Worker

This package owns mobile data purchase and data-plan query tasks after the orchestrator dispatches a data payload.

Start at `worker.py`.

## Runtime Flow

```text
policy gate -> extraction -> plan selection/query -> resolution -> source selection
  -> validation -> confirmation -> auth -> execution/schedule creation
```

`worker.py` handles policy gates, idempotency, context construction, and high-level action dispatch. Keep phase behavior in the pipeline, nodes, or focused ownership packages.

## Module Map

- `extraction/`: LLM extractor and data extraction prompt.
- `models/`: data payloads, context, plan models, and extraction contracts.
- `nodes/`: pipeline step entrypoints only.
- `pipeline/`: reusable pipeline runner.
- `pipeline_factory.py`: canonical data purchase and plan-query pipeline assembly.
- `plans/`: plan catalog service and selection/query helpers.
- `scheduling.py`: scheduled-data action constants, schedule pipeline steps, and schedule creation.

## Navigation Rules

- Put pipeline-step behavior in `nodes/`.
- Keep reusable catalog logic in `plans/`, extraction logic in `extraction/`, and schedule-specific behavior in `scheduling.py`.
- Import concrete modules directly; do not add compatibility wrappers for old paths.
- Do not mix airtime cleanup into this package; shared mobile-purchase cleanup should be a separate pass.
