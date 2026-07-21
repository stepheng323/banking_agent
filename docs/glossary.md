# Glossary

This glossary defines project-specific terms used across the Nenya AI banking agent docs. It is written for engineers who understand backend systems but may not know this codebase's vocabulary yet.

## System Boundaries

| Term | Meaning |
|---|---|
| `gateway` | The webhook-facing runtime. It validates WhatsApp/Telegram events, normalizes them, and publishes inbound work for chat processing. |
| `chat-worker` | The conversational runtime. It owns routing, planning, orchestration, short-lived conversation state, and dispatch to domain/product workers. |
| `transaction-worker` | The financial async runtime. It owns debit, payout, bill fulfillment, refund, and reconciliation queues. |
| `receipt-worker` | The outbound notification runtime. It renders receipts and sends user-visible provider messages. |
| Service boundary | A runtime ownership line. For example, `chat-worker` can decide that a transfer should execute, but `transaction-worker` owns provider-side financial execution. |
| Worker | A process or module that owns a focused unit of work. Runtime workers consume queues; domain workers are in-process product modules invoked by orchestration. |
| Provider | An external service integration, such as Mono, Flutterwave, OpenAI, WhatsApp, or Telegram. Mock/sandbox providers should be used locally. |

## Runtime Concepts

| Term | Meaning |
|---|---|
| Queue | A durable work handoff between runtime owners. In this repo, queue contracts are backed by Redis Streams. |
| Redis Stream | Redis' append-only stream structure used for queue-like work delivery, consumer groups, and replayable async handoffs. |
| Handoff | A controlled transfer of ownership from one runtime to another, usually through a stream message. |
| Checkpoint | A saved snapshot of conversation graph state. It lets the next user reply resume the same flow. |
| Durable state | State that must survive process restarts or later user turns. Examples: task state, pending interrupt, authorization context. |
| Ephemeral state | Temporary data that can be recomputed or discarded after the current execution pass. |
| Outbox | Staged user-visible messages or notification intents that will be delivered after graph execution. |
| Idempotency key | A stable key used to make retries safe. The same financial operation should not execute twice just because a message was retried. |

## AI And Orchestration Terms

| Term | Meaning |
|---|---|
| LangGraph | The graph runtime used by the chat orchestrator to move a turn through nodes such as ingest, gate, plan, advance, and finalize. |
| DAG | Directed acyclic graph: a set of nodes connected by one-way edges without loops in the dependency data. The code also has a control-flow graph that can loop intentionally. |
| Node | A named graph step that reads state and returns updates, such as `gate` or `advance`. |
| Edge | A route from one graph node to another. Conditional edges choose the next node from current state. |
| `END` | LangGraph's terminal marker for the current invocation. It does not end the conversation; it ends this processing pass. |
| Gate | The first routing layer inside the chat graph. It handles guardrails, deterministic fast paths, semantic routing, and planner handoff decisions. |
| `TurnDirective` | The authoritative routing contract for one turn. It records the owner, semantic decision, outcome kind, and the only graph transition instruction, `next_step`. |
| `RouteResolution` | The atomic pair of a validated `TurnDirective` and its non-routing state patch. It is the only supported way for routing producers to commit a directive. |
| Deterministic fast path | A rule-based route used when the system can decide safely without an LLM, such as a clear balance request. |
| Semantic router | A lightweight LLM classifier that chooses route shape when deterministic rules are not enough. It does not build detailed task payloads. |
| Planner | The LLM-backed task builder for ambiguous, mixed, or multi-step work. It produces typed task plans for execution. |
| Task | A unit of product work, such as checking balance, resolving a transfer recipient, buying airtime, or listing transactions. |
| `TaskSpec` | The persisted task container. It stores task id, type, dependencies, stage, and payload. |
| Wave | A group of tasks that can run after all earlier dependency waves have completed. Independent tasks can share a wave. |
| Blocker | A condition that prevents execution from continuing until the user answers or approves something. |
| `PendingInterrupt` | The durable representation of one active blocker, such as missing input, confirmation, or PIN/auth. |

## Banking Terms

| Term | Meaning |
|---|---|
| Source account | The linked account that funds a transfer, airtime purchase, data purchase, or scheduled payment. |
| Recipient | The person or destination account receiving money or value. |
| Beneficiary | A saved recipient record that can be reused for future transfers. |
| Funding plan | The selected source-account arrangement for a transaction. |
| Pooled funding | Funding one request from more than one source account. In this project it is explicit/opt-in and currently capped. |
| Funding review | The step that checks whether selected sources can cover the request and asks the user to resolve shortfalls or accept suggestions. |
| Confirmation | The user approval step after recipient, amount, source, and narration are ready to show. |
| Authorization | The security step after confirmation, usually PIN-based. |
| Refund | A compensating financial flow used when a debit, payout, bill purchase, or related operation must be reversed. |
| Reconciliation | Background checking that aligns internal records with provider-side status after async execution. |
| Receipt | A rendered proof/status message for a transaction or batch, delivered through the notification path. |

## Context Terms

| Term | Meaning |
|---|---|
| Context frame | A persisted user-visible surface, such as a transaction list or account list, that later follow-ups can refer to. |
| Referent memory | Short-lived memory of entities from prior surfaces or tasks, such as "the second transaction" or "Mum". |
| Stale context | Old context that might still be useful for terse follow-ups but should not steal fresh requests. |
| Active query session | A live transaction-query thread where follow-ups like "what about yesterday?" can continue the prior query intent. |

## Small Examples

| Example | What it demonstrates |
|---|---|
| User: "balance?" | Deterministic fast path can route directly to an account/balance task. |
| User: "what about yesterday?" after a transaction list | Semantic routing may classify this as an active query continuation. |
| User: "send 5k" | Transfer task blocks on missing recipient input and stores a `PendingInterrupt`. |
| User confirms a transfer, then enters PIN | Confirmation approves the task details; authorization permits execution for the exact idempotency keys. |
| User: "show the second one" after a transaction list | The system resolves "second one" through a context frame and referent memory. |
