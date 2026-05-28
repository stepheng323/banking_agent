# Gate Workflow

The gate is the orchestrator's pre-planner decision point. It handles deterministic fast paths, active-session follow-ups, unsupported-capability boundaries, and semantic routing before handing unresolved turns to the planner.

Start with `node.py`, then read `registry.py` to understand the ordered stage pipeline.

## Runtime Flow

```text
session_gate_direct_path
-> build GateContext
-> stale interrupt cleanup
-> ordered _GATE_STAGES
-> first stage with a result short-circuits
-> otherwise hand off to planner
```

The stage order in `registry.py` is part of behavior. Earlier stages own higher-confidence, lower-latency decisions; later stages own broader domain and semantic routing.

## Module Map

- `node.py`: LangGraph node entry point for the gate workflow.
- `registry.py`: single source of truth for ordered gate stages.
- `context.py`: shared `GateContext` passed through every stage.
- `routing.py`: routing constants and observability update helpers.
- `router_context.py`: compact context used by semantic routing decisions.
- `classifiers/`: deterministic text and state classifiers used by stages.
- `stages/`: short-circuitable pipeline steps grouped by routing concern.
- `direct_tasks.py`: helpers for building direct task payloads.
- `guardrails/cancellation.py`: cross-workflow cancellation policy and gate cancel phrase detection.
- `language.py`, `locale_state.py`, `mandate_state.py`, `interrupt_state.py`: focused state and policy helpers.
- `query_session_exit.py`: query-session exit detection.
- `support_identity.py`: support handoff identity/context helpers.
- `unsupported_capability_routing.py`: unsupported-capability routing helpers.

## Stage Groups

- Core safety/session checks: language switch, cancellation, gibberish, expired PIN, stale interrupts.
- Capability boundaries: mixed supported/unsupported requests, unsupported semantic routing, boundary follow-ups.
- Active context handling: schedules, resume prompts, data plan references, context-frame follow-ups, receipt/support thread follow-ups.
- Direct domain handling: balance, account, beneficiary, airtime, data, receipt, and support issue requests.
- Planner guardrails: banking ambiguity, deterministic meta routes, query/transfer guards.
- Semantic fallback: semantic router dispatch before final planner handoff.

## Navigation Rules

- Add new gate behavior as a stage when it can decide before the planner.
- Keep stage order changes in `registry.py` explicit and reviewed as behavior changes.
- Put reusable deterministic predicates in `classifiers/` or `../../guardrails/` depending on ownership; keep one-off stage helpers beside the stage.
- Do not import from `nodes/` or old gate paths; workflow internals should import concrete modules in this package.
