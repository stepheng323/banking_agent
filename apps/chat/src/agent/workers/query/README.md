# Query Worker

This package owns transaction and account query handling after the orchestrator dispatches a query task.

## Runtime Path

```text
worker.py -> pipeline.py -> nodes/extraction.py -> nodes/execution.py
```

`worker.py` manages query-session persistence and progress copy. `pipeline.py` keeps the ordered node execution small; the node modules should stay focused on pipeline-step behavior.

## Directory Map

- `models/`: typed query contracts, filters, result shapes, surfaces, and extraction models.
- `compiler/`: converts parsed query intent into executable query contracts, capability decisions, filters, operations, aggregations, and time ranges.
- `continuations/`: classifies and resolves follow-up turns against active result state, pending clarifications, and grounded frames.
- `grounding/`: stores and restores typed query frames used to anchor follow-ups.
- `handlers/`: domain answer handlers for transactions, analytics, affordability, beneficiaries, and time comparisons.
- `presentation/`: surface planning, response formatting, headings, direct-answer surfaces, transaction lists, grouped summaries, and selections.
- `services/analysis/`: local analysis helpers such as transaction narration classification.
- `services/answers/`: answer strategy, direct fact answers, no-result explanations, existence answers, and coverage responses.
- `services/conversation/`: query conversation target detection and update shaping.
- `services/fetching/`: transaction fetching, filtering, local mirror access, and mirror fallbacks.
- `services/parsing/`: LLM parser entrypoint and parse normalization.
- `services/reasoning/`: semantic reasoner, reasoner schemas, and deterministic query shortcuts.
- `prompts/`: query-specific LLM prompt text.
- `utils/`: date and timezone helpers.

## Navigation Rules

- Import concrete modules directly; do not add compatibility wrappers for old service paths.
- Keep parser/compiler code separate from fetch/execution code.
- Put user-facing copy and surface shape decisions in `presentation/`, not in compiler modules.
- Add new continuation behavior under `continuations/` unless it is a generic query frame primitive, which belongs in `grounding/`.
