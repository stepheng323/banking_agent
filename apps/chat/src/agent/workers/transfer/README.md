# Transfer Worker

This package owns money-transfer tasks after the orchestrator dispatches a transfer payload.

Start at `worker.py`.

## Runtime Path

```text
worker.py -> pipeline_factory.py -> pipeline/base.py
  -> extraction -> resolution -> validation -> funding -> confirmation -> payout preparation -> execution
```

`worker.py` handles policy gates, scheduling dispatch, context construction, and pipeline invocation. Keep transaction-step behavior inside the pipeline or node modules rather than expanding the worker.

## Directory Map

- `authorization/`: PIN token persistence helpers used by transfer and scheduled-transfer auth flows.
- `extraction/`: deterministic parsers, slot selection helpers, extraction-result merge logic, LLM extractor, and transfer extraction prompt.
- `models/`: transfer payloads, context, gates, entities, and extraction contracts.
- `nodes/`: pipeline step entrypoints only.
- `pipeline/`: reusable pipeline runner and step sequencing.
- `pipeline_factory.py`: canonical transfer pipeline assembly.
- `resolution/`: recipient resolution, saved beneficiary matching, bank-code helpers, and self-transfer validation.
- `scheduling.py`: scheduled-transfer actions and CRUD flow.
- `validation/`: reusable transfer validation service used by the worker and validation node.

## Navigation Rules

- Put new transfer-step behavior in the node that owns that phase.
- Keep reusable extraction logic in `extraction/`, recipient/account matching in `resolution/`, and validation rules in `validation/`.
- Import concrete modules directly; do not add compatibility wrappers for old paths.
- Keep scheduling-specific behavior in `scheduling.py` unless it is a reusable transfer pipeline step.
