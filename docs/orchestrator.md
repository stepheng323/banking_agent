# Orchestrator

The chat runtime uses LangGraph to coordinate message ingestion, routing, planning, task execution, durable interrupts, and user-visible replies.

For the detailed graph and task-DAG internals, see [DAG And Orchestration Internals](dag-and-orchestration-internals.md). For service boundaries and queues, see [Architecture](architecture.md). For gate and semantic-router detail, see [Gate And Semantic Routing](gate-and-routing.md). For money movement, see [Money Movement](money-movement.md).

## Terms Before You Read

See [Glossary](glossary.md) for the full vocabulary. This page uses these terms most:

| Term | Meaning here |
|---|---|
| Graph | The LangGraph workflow that moves one inbound event through routing, planning, execution, and finalization. |
| Gate | The first routing layer inside the graph. It handles high-confidence direct routes and decides when semantic routing or planning is needed. |
| Planner | The LLM-backed component that turns ambiguous or multi-step user requests into typed tasks. |
| Task execution | The phase where typed tasks are sent through domain executors and workers. |
| Interrupt | A durable pause that waits for the user, such as missing input, confirmation, or PIN/auth. |
| Checkpoint | The saved graph state that lets the next user reply resume the same flow. |

Example: "send 5k" may become a transfer task, then pause on an interrupt asking for the recipient. When the user replies, the checkpoint is loaded and execution resumes instead of starting over.

## Complex Systems Map

Use these docs as the main entry points for the highest-complexity areas:

| Area | Start here |
|---|---|
| Top-level LangGraph DAG, task dependency waves, interrupts, and context memory | [DAG And Orchestration Internals](dag-and-orchestration-internals.md) |
| Gate stage order, deterministic fast paths, semantic routing, stale context arbitration, and route metadata | [Gate And Semantic Routing](gate-and-routing.md) |
| Runtime service boundaries, queues, streams, and worker ownership | [Architecture](architecture.md) |
| Money movement, confirmation, funding, async financial jobs, and receipts | [Money Movement](money-movement.md) |
| Local stack operation, environment setup, and operational debugging | [Local Development](local-development.md), [Operations](operations.md) |

## Runtime Lifecycle

At a high level, one inbound turn moves through:

```text
message consumer -> graph invocation -> lifecycle ingest -> gate -> planner -> execution -> finalize/outbox
```

The message consumer handles dedupe, per-thread locking, typing heartbeat ownership, and safe fallback behavior. The graph then receives a typed state object and returns state updates plus outbox intents. The exact LangGraph nodes, conditional routes, and END conditions are documented in [DAG And Orchestration Internals](dag-and-orchestration-internals.md).

## Gate Routing

The gate is the first routing layer inside the graph. It handles high-confidence deterministic paths and decides when to call semantic routing.

Typical gate responsibilities:

- honor active interrupts and hard guardrails;
- route deterministic account/balance/query/transaction fast paths when confidence is high;
- defer ambiguous, multilingual, stale-context, or mixed transactional turns to semantic routing;
- preserve active query continuation semantics without letting keyword heuristics override the LLM-owned route when correctness needs semantic judgment.

Stage order is part of the routing contract. The gate is a layered, first-match engine; broad deterministic stages must decline when confidence is not high enough, otherwise they can steal turns from semantic routing or planner fallback. The full stage registry, semantic-router rules, route metadata, and debugging checklist are documented in [Gate And Semantic Routing](gate-and-routing.md).

Gate workflow code starts under `apps/chat/src/agent/orchestrator/workflows/gate/`. In the top-level graph, the gate routes to `advance`, `handle_interrupt`, or `plan`; see [DAG And Orchestration Internals](dag-and-orchestration-internals.md#top-level-graph).

## Planner And Semantic Router

The planner converts natural language into typed task graphs. The semantic router classifies whether a turn is a domain request, continuation, mixed request, support request, or unsupported capability before planner fallback.

Important behavior:

- planner output is typed before execution;
- task parameters are executor/action specific;
- semantic fallback is used when deterministic parsing is not enough, especially for active query sessions, stale context, multilingual turns, and mixed intents;
- planner quality checks and normalizers can repair or mark dirty output before execution.

Planner code starts under `apps/chat/src/agent/orchestrator/workflows/planner/`. Semantic-router prompt and model code lives there, while gate-stage integration lives under `apps/chat/src/agent/orchestrator/workflows/gate/stages/semantic_router_stage.py`. Planner task output is converted into `TaskSpec`s and dependency waves before execution; see [Planner To Task DAG](dag-and-orchestration-internals.md#planner-to-task-dag).

## Execution Waves

Execution runs planned tasks through domain-specific executors and workers. Domain workers inspect typed task payloads plus loaded context, then return a small set of outcomes:

| Outcome              | Meaning                                            |
| -------------------- | -------------------------------------------------- |
| `NEEDS_INPUT`        | Required task details are missing or ambiguous     |
| `NEEDS_CONFIRMATION` | Complete money-moving task needs user confirmation |
| `NEEDS_AUTH`         | Confirmed task needs PIN/authorization             |
| `OK`                 | Task completed or produced a final response        |
| `FAILED`             | Task failed or was rejected                        |

Workers do not own durable conversation interrupts. They return domain artifacts such as required fields, prompts, confirmation snapshots, patches, receipts, and summaries. The orchestrator applies patches and persists the conversation checkpoint. The wave loop, blocker arbitration, recipient review, and batch funding gates are documented in [Execution Wave Loop](dag-and-orchestration-internals.md#execution-wave-loop).

## Pending Interrupts

The orchestrator persists one active `PendingInterrupt` at a time. When several tasks in the same wave produce blockers, the priority is:

```text
missing input > confirmation > auth
```

That means a task that still needs input intentionally defers a sibling auth request. Multiple confirmation/auth blockers in the same wave are collapsed into one batch checkpoint with task IDs and snapshots preserved.

Interrupts are used for:

- missing transfer/account/bill details;
- recipient review;
- funding adjustment and funding suggestion acceptance;
- transaction confirmation;
- PIN/auth;
- query and context-frame continuations.

The interrupt resume path is documented in [Interrupt And Resume Lifecycle](dag-and-orchestration-internals.md#interrupt-and-resume-lifecycle).

## Context Frames And Follow-Ups

Context frames preserve the current user-facing surface: query results, linked accounts, beneficiaries, schedules, receipt candidates, transaction summaries, and other read-only lists.

They support follow-ups such as:

- "show them";
- "fetch it again";
- "what about yesterday";
- "set 2 as default";
- "do the last transfer again".

Refresh/replay behavior is intentionally separated from stale support/receipt context so fresh user requests are not stolen by old context.

The state surfaces and stale-context safety rules are documented in [Context And Memory Surfaces](dag-and-orchestration-internals.md#context-and-memory-surfaces).

## State And Checkpoint Safety

LangGraph checkpoints are short-lived conversation state, not long-term audit storage. Runtime state uses TTLs and legacy hydration protection so old serialized state cannot crash newer schema versions.

Important rules:

- stale checkpoints should expire or be dropped safely;
- authorization state is bound to exact task idempotency keys;
- boolean-only `pin_verified` is not enough to authorize execution;
- any task edit that changes amount, recipient, source, or funding clears stale funding/confirmation state.

## Main Code Paths

| Area                           | Path                                                    |
| ------------------------------ | ------------------------------------------------------- |
| Runtime agent                  | `apps/chat/src/agent/orchestrator/agent.py`             |
| Graph handler                  | `apps/chat/src/agent/orchestrator/graph/`               |
| DAG internals docs             | `docs/dag-and-orchestration-internals.md`               |
| Lifecycle                      | `apps/chat/src/agent/orchestrator/workflows/lifecycle/` |
| Gate                           | `apps/chat/src/agent/orchestrator/workflows/gate/`      |
| Gate and semantic routing docs | `docs/gate-and-routing.md`                              |
| Planner                        | `apps/chat/src/agent/orchestrator/workflows/planner/`   |
| Interrupts                     | `apps/chat/src/agent/orchestrator/workflows/interrupt/` |
| Execution                      | `apps/chat/src/agent/orchestrator/workflows/execution/` |
| Shared state models            | `apps/chat/src/agent/orchestrator/models/`              |
