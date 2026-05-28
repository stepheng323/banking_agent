# Shared Services

This package contains shared application services used by gateways and workers. Prefer concrete modules over package-level imports.

## Module Families

- `auth/`: PIN and authorization services.
- `onboarding/`: onboarding, account linking, BVN verification, mandates, and session runtime.
- `funding/`: source-account matching, batch allocation, pooling, and funding plans.
- `scheduling/`: shared scheduled-action helpers.
- `task_queue/`: shared task queue service helpers.
- `verification/`: verification service helpers.
- `task_planner*.py`: task-planner runtime, prompt compilation, model wiring, prompt sections, normalizers, and observability.
- `confirmation_*.py`: confirmation reply models, phrase lists, guardrails, and classifier logic.
- `conversation_responder*.py` and `conversation_grounding.py`: conversational reply generation, unsupported-response handling, grounding, and prompt text.
- `unsupported_capability_*.py`: unsupported-capability models, registry, deterministic detection, semantic classification, and presentation.
- `delivery_*.py`: delivery orchestration, ledger persistence, background actionable handling, and delivery models.
- `async_*.py`: async completion helpers and recent-batch reference tracking.
- `channel_link_*.py`: channel-link PIN authorization contracts and completion flow.
- `context_*.py`: context loading and Redis/user-data state helpers.

## Navigation Rules

- Import the concrete module that owns the behavior; avoid package-level service imports.
- Do not add compatibility wrappers for old module names. Update callers to the owning module instead.
- Keep pure contracts/models separate from runtime flows that open repositories, cache clients, or external services.
- Add a new module only when it owns a clear service family or removes meaningful confusion from an existing file.
