# Bounded-Jarvis conversation readiness

`jarvis-conversations` is the release-oriented benchmark for conversational
competence inside the banking capabilities the assistant actually supports.
It is not a general-intelligence benchmark. Every case is evaluated against
typed routing, retained state, deterministic effects and the LLM call budget.

This suite is graph-backed because its purpose is to exercise retained
conversation state, domain workers and real presentation. Run it against the
seeded graph when checking provider-shaped responses and checkpoint hydration:

```bash
READINESS_VERBOSE_EVENTS=false uv run python scripts/readiness.py \
  --mode dry-run \
  --scenario jarvis-conversations \
  --phone <demo-phone> \
  --reset-session \
  --json-output /tmp/jarvis-conversations-live.json \
  --transcript-output /tmp/jarvis-conversations-live.txt
```

The deterministic harness intentionally skips these graph-only turns. Use the
existing `adversarial-conversations` suite for zero-provider-call contract
smoke checks.

The suite has ten independently reported dimensions:

- intent continuity: a contrastive follow-up refines the active query;
- referential continuity: an ordinal selection uses the retained result frame;
- correction and repair: one scope field changes while the rest is preserved;
- interruption and resume: a read-only question does not lose a pending action;
- mixed input: all unresolved batch fields remain visible and ordered;
- partial failure: incomplete or failed work is never reported as complete;
- evidence grounding: displayed evidence comes from the discussed result;
- safety controls: confirmation and PIN cannot be bypassed by wording;
- recovery: stale, invalid and cancelled references fail safely;
- latency fast paths: safe deterministic reads do not invoke an LLM.

Each dimension is a small multi-turn scenario. The harness records category,
state snapshot, route signature, ordered model-call chain, call-budget status,
money-moving jobs and response assertions. The report therefore answers both
“did it pass?” and “which Jarvis capability regressed?” without logging bank
identifiers, transaction references, amounts or raw payloads.

This suite is deliberately stricter than a prose demo. A response can sound
helpful and still fail if it loses the active query, invents a currency value,
executes before authorization, drops a sibling batch task, or spends an extra
model call on a deterministic follow-up.
