# Orchestrator Task Handlers

This package owns post-planner task-family handlers used by `workflows/execution`.

Start with `runtime.py` for the execution context and aggregation helpers, then follow the task-family module that matches the task being advanced.

## Runtime Flow

```text
execution workflow
-> runtime context
-> task-family handler
-> domain worker/service
-> context-frame or follow-up updates
-> aggregated state updates
```

## Module Map

- `runtime.py`: execution context, aggregation, service lookup, and common worker dispatch helpers.
- `transfer.py`: transfer worker routing, schedule list handling, and transfer-specific result updates.
- `purchase.py`: airtime/data worker routing and purchase result updates.
- `query.py`: query worker routing, query pagination payloads, and query result context frames.
- `account_beneficiary.py`: account and beneficiary task handling plus list context frames.
- `support.py`: support worker routing and support result conversion.
- `session.py`: orchestrator session actions such as resume/dismiss.
- `context_frames.py`: shared context-frame builders used by multiple task-family modules.

Keep execution behavior grouped by task family. Keep `context_frames.py` as the shared frame-building surface until one family needs enough frame-specific behavior to justify its own module.
