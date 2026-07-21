# Gate And Semantic Routing

This document explains how an inbound turn is routed once the chat-worker has invoked the orchestrator graph. It focuses on the gate and the semantic router because they are the highest-complexity parts of the conversation runtime.

For the current end-to-end model, see [Conversation Runtime Guide](conversation-runtime.md). For the full graph lifecycle, see [Orchestrator](orchestrator.md). For the exact LangGraph edges around the gate node and the task-DAG execution loop, see [DAG And Orchestration Internals](dag-and-orchestration-internals.md). For service and queue boundaries, see [Architecture](architecture.md).

## Terms Before You Read

See [Glossary](glossary.md) for the full vocabulary. This page uses these terms most:

| Term | Meaning here |
|---|---|
| Deterministic routing | Rule-based routing used when the text and state are clear enough to avoid an LLM call. |
| Fast path | A narrow deterministic route that bypasses planner task construction. |
| Semantic routing | Lightweight LLM classification used to choose route shape when rules are not enough. |
| Planner handoff | Letting the planner build typed tasks because the turn is mixed, ambiguous, or planner-owned. |
| Route metadata | Debug fields that record who routed the turn and why. |
| Stale context | Old receipt/support/context-frame state that can help terse follow-ups but must not steal fresh requests. |

Examples: "balance?" can use a deterministic fast path. "what about yesterday?" after a transaction list may need semantic routing as an active query continuation. "send 5k to Adebayo and buy 2k airtime" should hand off to the planner because it has mixed work.

## Mental Model

The gate is a layered, first-match router. It receives the current typed graph state, the inbound message, loaded user context, and any live interrupt. It then chooses one of these outcomes:

| Outcome | Meaning |
|---|---|
| Direct response | Return text immediately, for example cancel, greeting, or guardrail copy |
| Direct domain task | Build a single task without planner output, for example account list or balance check |
| Planner handoff | Let the planner build one or more typed tasks |
| Semantic-router domain dispatch | Use the LLM router to classify the turn, then build the matching direct task |
| Policy block | Stop unsupported or unsafe capability requests |
| Interrupt handoff | Send a live durable interrupt to the interrupt handler |

Every selected outcome is committed through a `TurnDirective` plus `RouteResolution`. `TurnDirective.next_step` is the sole graph-control instruction; a stage cannot independently steer the graph by writing a response, task list, or interrupt field.

The semantic router is not the same thing as the planner. It is a lightweight LLM classifier used before planner fallback when deterministic routing is not reliable enough. Its job is to decide the route shape: query continuation, account request, fresh transfer, mixed planner request, support follow-up, unsupported capability, and similar top-level intents. When a bounded displayed context frame is eligible, the same call also returns a sparse continuation selector; local code resolves that selector against stable frame references. There is no second context-arbitration LLM call.

The planner owns task construction for mixed or ambiguous transactional work. The semantic router owns top-level route arbitration.

Inside the top-level LangGraph DAG, the gate can commit these graph-level exits:

| Exit | Condition | Meaning |
|---|---|---|
| `advance` | `turn_directive.next_step=advance` | A validated task dispatch has a current wave |
| `handle_interrupt` | `turn_directive.next_step=handle_interrupt` | A live pending interrupt is handed to its resolver |
| `plan` | `turn_directive.next_step=plan` | Planner owns typed task construction |
| `finalize` | `turn_directive.next_step=finalize` | A direct response or policy block is ready for the outbox |
| `end` | `turn_directive.next_step=end` | The current turn is intentionally complete |

The detailed graph route table is documented in [Top-Level Graph](dag-and-orchestration-internals.md#top-level-graph).

## Gate Engine Contract

The gate registry is declared in `apps/chat/src/agent/orchestrator/workflows/gate/stage_specs.py`. Each stage is a `GateHandlerSpec` with:

| Field | Purpose |
|---|---|
| `id` | Stable stage identifier used in traces and tests |
| `layer` | High-level ordered routing phase |
| `priority` | Order within the layer |
| `owner` | Conceptual owner: guardrail, semantic router, planner, etc. |
| `outcome_kind` | Expected update shape for reviewability |
| `may_call_llm` | Whether this stage is allowed to call a model |
| `eligibility` | Cheap pre-check before the handler runs |

`run_gate_engine(...)` sorts handlers by `(layer, priority)` and returns the first handler that returns updates. If no handler matches, the engine falls through to `planner_fallback`.

This is the main safety property: every stage must either be high-confidence or decline cleanly. A broad deterministic stage can steal messages from later semantic routing, so stage scope and ordering are part of the product contract.

## Stage Layers

Current layer order:

| Layer | Responsibility |
|---|---|
| `preflight_cleanup` | Clear dead pending interrupts before routing starts |
| `hard_guardrails` | Deterministic cancellation, language switch, gibberish, expired PIN handling |
| `capability_guards` | Unsupported-capability detection and boundary follow-ups |
| `session_resume` | Stashed-session resume and scheduled-read routing |
| `specialized_fastpaths` | Narrow product fast paths such as visible data-plan purchases |
| `context_followups` | Context frames, stale receipt/support context, beneficiary suggestions, read-only refreshes |
| `domain_fastpaths` | High-confidence account, balance, beneficiary, airtime, data, query, and transfer routes |
| `semantic_routing` | LLM route arbitration before planner fallback |
| `planner_fallback` | Planner-owned route when the gate cannot safely decide |

Important ordering rules:

- Hard guardrails run before any LLM route. Cancel, expired PIN, and obvious garbage should not wait for semantic routing.
- Stale context arbitration runs before receipt/support/context follow-ups. Non-terse fresh requests must not be stolen by old receipt or support state.
- Domain fast paths run only when confidence is high. If a fast path is unsure, it should decline and let semantic routing or planner fallback handle the turn.
- Semantic routing runs after deterministic fast paths and before planner fallback.
- Planner fallback is the catch-all for planner-owned or unresolved turns.

## Current Stage Registry

The exact stage list changes as routing evolves, but the current registry is:

| Layer | Stage | Owner | LLM | Purpose |
|---|---|---|---|---|
| `preflight_cleanup` | `stale_interrupt_cleanup` | guardrail | no | Clear dead pending interrupts |
| `hard_guardrails` | `language_switch` | guardrail | no | Deterministic locale switch commands |
| `hard_guardrails` | `cancel` | guardrail | no | Explicit cancellation and cleanup |
| `hard_guardrails` | `gibberish_filter` | guardrail | no | Reject obvious gibberish |
| `hard_guardrails` | `expired_pin` | guardrail | no | Handle stale PIN callbacks |
| `capability_guards` | `mixed_supported_unsupported_capability` | guardrail | no | Split supported banking clauses from unsupported asks |
| `capability_guards` | `capability_boundary_followup` | guardrail | yes | Answer follow-ups to recent unsupported refusals |
| `capability_guards` | `semantic_unsupported_capability` | guardrail | yes | Semantic unsupported-capability fallback |
| `session_resume` | `schedule_read_router` | semantic_router | yes | Route simple scheduled-transaction reads |
| `session_resume` | `resume_prompt_action` | guardrail | yes | Resolve stashed-session prompt replies |
| `specialized_fastpaths` | `data_plan_reference_purchase` | guardrail | no | Buy a visible referenced data plan |
| `specialized_fastpaths` | `data_plan_query` | guardrail | no | Deterministic data-plan catalog lookup |
| `context_followups` | `stale_context_arbitration` | semantic_router | yes | Arbitrate non-terse turns while stale context exists |
| `context_followups` | `recent_transaction_support_request` | guardrail | no | Route support requests against a recent transaction |
| `context_followups` | `support_issue_request` | guardrail | no | Route clear transaction/ticket problem statements |
| `context_followups` | `context_frame_followup` | semantic_router | no | Resolve deterministic selectors; defer ambiguous frame language to the semantic router |
| `context_followups` | `receipt_thread_followup` | guardrail | no | Route active receipt-thread selectors |
| `context_followups` | `support_context_followup` | guardrail | no | Route follow-ups using recent support context |
| `context_followups` | `receipt_request` | guardrail | no | Route recent batch receipt requests |
| `context_followups` | `beneficiary_suggestion` | guardrail | no | Resolve post-transfer beneficiary-save suggestions |
| `context_followups` | `contextual_worker_followup` | guardrail | yes | Answer non-actionable acknowledgements after prior results |
| `domain_fastpaths` | `banking_ambiguity` | guardrail | no | Clarify deterministically ambiguous banking phrases |
| `domain_fastpaths` | `balance_direct` | guardrail | no | High-confidence balance requests |
| `domain_fastpaths` | `account_domain` | guardrail | no | High-confidence account-domain requests |
| `domain_fastpaths` | `beneficiary_domain` | guardrail | no | High-confidence beneficiary-domain requests |
| `domain_fastpaths` | `airtime_domain` | guardrail | no | High-confidence airtime purchases |
| `domain_fastpaths` | `data_domain` | guardrail | no | High-confidence data purchases |
| `domain_fastpaths` | `deterministic_meta` | guardrail | no | Greetings, identity, and simple meta turns |
| `domain_fastpaths` | `query_and_transfer_domain_guards` | guardrail | no | High-confidence query and transfer fast paths |
| `semantic_routing` | `semantic_router` | semantic_router | yes | Top-level semantic routing before planner fallback |

When updating the registry, also update stage-order tests and this table if the behavior changes materially.

## Deterministic Fast Paths

Deterministic routing is used when the system can decide cheaply and safely. Examples:

- exact cancel and language-switch commands;
- numeric selections for a live options prompt;
- direct account and balance requests with strong lexical evidence;
- clear data-plan reference purchases from a visible plan list;
- terse receipt selectors in an active receipt thread.

Fast paths are not semantic authority. They should decline when:

- the message is multilingual or phrased outside the narrow parser;
- stale receipt/support/context state exists and the message is non-terse;
- the text contains fresh transactional slots that could be money movement;
- a parse is partial, ambiguous, or low confidence;
- an active query session needs semantic continuation judgment.

The fallback order after a deterministic decline is normally semantic routing, then planner fallback.

## Semantic Router Responsibilities

The semantic router classifies route shape, not detailed task payloads. It can produce:

| Decision Shape | Typical Handling |
|---|---|
| `domain_query` | Build a query task; preserve continuation/new mode |
| `domain_account` | Build an account task |
| `domain_beneficiary` | Build a beneficiary task |
| `domain_transfer` | Build a single transfer-domain task unless mixed executors require planner |
| `domain_airtime` | Build an airtime task unless mixed executors require planner |
| `domain_data` | Build a data task unless mixed executors require planner |
| `domain_schedule` | Build schedule read task or hand off schedule mutation to planner |
| `domain_support` | Build support task, with replay-modifier vetoes where needed |
| `planner_mixed` / executor hints | Hand off to planner with expected transaction executors |
| `planner_ambiguous` | Hand off to planner or ask clarification depending on context |
| `direct_reply` | Return a direct answer when appropriate |
| `cancel` | Use cancellation reset/clarification logic |

The router receives a compact turn context summary plus optional account/beneficiary previews when the message appears to need them. It also receives `expected_transaction_executors` and non-authoritative routing hints.

## Routing Hints

Routing hints are appended to the router context as candidate domains:

```text
Routing hints are non-authoritative guardrail hints. Use them only when they match the user intent.
- candidate_domain=query; reason=active_query_session; source=...
```

They help the LLM interpret active state without forcing a result. A hint should never override the user text. If the user starts a fresh transfer during an active query session, the router should return transfer/new, not query/continuation.

Use hints for context, not control. Control still comes from the router decision plus deterministic safety checks after the router returns.

## When Semantic Routing Runs

The semantic-router stage can run when:

- the message is not a quote;
- a task planner is available;
- there is a live pending interrupt that is safe for semantic routing, or the message is non-empty.

The stage skips semantic routing for some live interrupts:

- confirmation and auth interrupts;
- numeric input selections;
- input interrupts that look like schedule-read or support-problem requests handled earlier;
- deterministic active-flow questions already classified with a known question type.

This protects transactional confirmation/auth flows from broad semantic reinterpretation while still allowing semantic fallback for input-stage ambiguity where it is safe.

## Active Query Sessions

Active query sessions are semantic context, not deterministic ownership. The router should classify:

- "what about yesterday" as query continuation;
- "show me" after a query summary as evidence/list continuation;
- "send 5k to Adebayo" as a fresh transfer;
- "send 10k to Adebayo and buy 2k airtime" as a mixed planner request.

If a non-query domain wins while a query session is active, the gate clears the active query session and records query-session exit updates. This prevents old query context from stealing fresh banking work.

## Stale Context Arbitration

Receipt threads, support references, and context frames can outlive the turn that created them. That is useful for terse follow-ups, but dangerous for fresh commands.

The stale-context arbitration rule is:

- strict terse selectors can remain deterministic;
- non-terse, slot-bearing, multilingual, or fresh-intent messages go to semantic routing first;
- an eligible context-frame summary is appended to that router call, and it returns a typed action plus visible ordinal or masked-label selectors;
- frame IDs, database IDs, full account numbers, and hidden result rows are never sent to the router; deterministic materialization performs the final lookup;
- when semantic routing chooses a fresh banking flow, ephemeral receipt/support state is cleared;
- long-lived useful references can remain if they are not controlling the current route.

This is why broad receipt/support matching must not run before arbitration.

## Planner Handoff

The gate hands off to the planner when:

- no deterministic or semantic stage matched;
- semantic routing detects mixed transaction executors;
- the route is schedule mutation rather than simple schedule read;
- the semantic router returns only executor hints;
- the turn is planner-owned or ambiguous enough to require typed task construction.

Planner handoff should preserve route metadata and expected executors so readiness reports can distinguish planner-owned work from semantic-router-owned direct dispatch.

After planner handoff, planner output is converted into `TaskSpec`s and topological execution waves. That task-DAG boundary is documented in [Planner To Task DAG](dag-and-orchestration-internals.md#planner-to-task-dag).

## Route Metadata

`TurnDirective` is the primary routing/debugging surface. Its fields are:

| Field | Meaning |
|---|---|
| `owner` | Which layer owns the route, such as `guardrail`, `semantic_router`, or `planner` |
| `decision` | The normalized semantic reason, such as `domain_transfer`, `planner_handoff`, or `capability_blocked` |
| `outcome_kind` | Direct response, task dispatch, planner handoff, interrupt handoff, or policy block |
| `next_step` | The sole graph transition instruction |
| `target_domain`, `mode`, `source`, `path_shape` | Domain, continuation semantics, origin, and readiness/debug label |

Flat `routing_*` labels are derived telemetry projections only. `direct_path_triggered` and `semantic_path_shape` are not routing state.

When debugging a bad route, start with these fields before reading prompts.

## Debugging Checklist

For a routing bug, inspect in this order:

1. Confirm whether a live `PendingInterrupt` existed.
2. Check `matched_handler_id` and `matched_layer` in the gate trace.
3. Check `turn_directive`: owner, decision, outcome kind, target domain, and especially `next_step`.
4. If a deterministic stage matched, verify whether it should have declined.
5. If semantic routing ran, inspect `semantic_router_llm_call`, the compact router context, and any routing hints.
6. If planner fallback ran, inspect planner prompt profile, expected executors, and planner quality/dirty reasons.
7. For stale-context bugs, inspect receipt/support/context-frame state and whether `stale_context_arbitration` ran first.
8. For active-query bugs, inspect query-session state and whether the router chose continuation or new mode.

Useful log/events:

- `gate_engine_trace_summary`
- `gate_dispatch_to_planner`
- `gate_semantic_router_domain_dispatch`
- `gate_semantic_router_mixed_veto`
- `gate_semantic_router_expected_executors`
- `semantic_router_llm_call`
- `orchestrator_turn_summary`
- `orchestrator_route_metrics`

## Test Coverage To Update With Routing Changes

Routing changes should normally include focused tests around:

- gate stage order and handler ownership;
- active query continuation vs fresh-domain routing;
- stale receipt/support/context arbitration;
- deterministic fast-path decline behavior;
- multilingual semantic-router cases;
- planner fallback and expected-executor preservation;
- route metadata and readiness metrics.

Relevant test areas include:

- `tests/orchestrator/test_gate_engine.py`
- `tests/orchestrator/test_gate_fastpath_i18n.py`
- `tests/orchestrator/test_context_frame_followups.py`
- `tests/shared/test_semantic_router_prompt_replay_parity.py`
- `tests/shared/test_semantic_router_live_eval.py`
- `tests/orchestrator/conversation_harness.py`

## Code Map

| Area | Path |
|---|---|
| Gate registry | `apps/chat/src/agent/orchestrator/workflows/gate/stage_specs.py` |
| Gate engine | `apps/chat/src/agent/orchestrator/workflows/gate/core/engine.py` |
| Gate contracts | `apps/chat/src/agent/orchestrator/workflows/gate/core/contracts.py` |
| Gate context/state view | `apps/chat/src/agent/orchestrator/workflows/gate/core/context.py`, `state/state_view.py` |
| Canonical routing contract | `apps/chat/src/agent/orchestrator/models/turn_directive.py` |
| Semantic-router stage | `apps/chat/src/agent/orchestrator/workflows/gate/stages/semantic_routing/pipeline.py` |
| Semantic route controls | `apps/chat/src/agent/orchestrator/workflows/gate/stages/semantic_routing/control_handlers.py` |
| Semantic domain dispatch | `apps/chat/src/agent/orchestrator/workflows/gate/stages/semantic_routing/domain_dispatch.py` |
| Router context | `apps/chat/src/agent/orchestrator/workflows/gate/utils/router_context.py` |
| Semantic-router prompt | `apps/chat/src/agent/orchestrator/workflows/gate/utils/semantic_router_prompt_compiler.py` |
| Planner core | `apps/chat/src/agent/orchestrator/workflows/planner/` |
| Graph and task-DAG internals | `docs/dag-and-orchestration-internals.md` |
