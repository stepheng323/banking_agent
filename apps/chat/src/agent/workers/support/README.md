# Support Worker

Start at `worker.py`. It is the support worker entry point used by the orchestrator and shows the runtime order.

## Runtime Flow

```text
policy check
-> pending reference follow-up
-> classification
-> diagnostic router
-> micro resolver
-> transaction resolution
-> handler dispatch
-> result builders
```

## Module Map

- `worker.py`: top-level support task flow and coordination.
- `diagnostic_agent.py` and `diagnostic_routing.py`: optional diagnostic-agent decisioning and conversion into worker routes.
- `reference_flow.py` and `reference_selection.py`: recent batch references, receipt selection, and pending-reference state.
- `resolver.py`: transaction lookup and transaction-to-dict normalization.
- `handlers/`: concrete support intent handlers for status, retry, reversal, fraud, receipts, escalation, and tickets.
- `results.py`: support result shaping, receipt jobs, ticket creation responses, and retry handoff payloads.
- `capabilities.py`: support action names, limits, and policy limitation messaging.

Keep behavior under the module that owns when it runs. Avoid compatibility wrappers for old support paths; update callers to concrete modules.
