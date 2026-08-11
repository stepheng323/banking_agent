# Adversarial conversation readiness

The readiness harness evaluates the banking assistant at the level users
experience: a sequence of turns, the active state after each turn, and any
durable or money-moving effects. It does not use generated prose as the
correctness oracle.

The existing `robustness` suite remains the fast utterance-mutation baseline.
The `adversarial-conversations` suite is the curated seed corpus for
stateful, safety-critical conversations. Each seed can later be expanded with
conversation mutations and pairwise locale/channel/fault combinations.

For the release-oriented bounded-Jarvis scorecard, use the separate
[`bounded-jarvis-readiness.md`](bounded-jarvis-readiness.md) suite. It measures
continuity, repair, interruption/resume, mixed input, evidence grounding,
recovery, safety and fast-path latency as named dimensions.

Run the deterministic seed corpus with:

```bash
READINESS_VERBOSE_EVENTS=false uv run python scripts/readiness.py \
  --mode deterministic \
  --scenario adversarial-conversations \
  --json-output /tmp/adversarial-conversations.json \
  --transcript-output /tmp/adversarial-conversations.txt
```

Use `--mode dry-run --phone <demo-phone> --reset-session` when the scenario
needs the full graph, seeded repositories, provider-shaped responses or live
LLM routing. Live-provider runs are opt-in; deterministic runs must remain
safe to execute in CI.

To exercise a conversation mutation across the same seed corpus, pass one of
the registered mutations:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/readiness.py \
  --mode deterministic \
  --scenario adversarial-conversations \
  --conversation-mutation repeat_last \
  --repeat 10
```

Available mutations are `insert_ack_before_last`,
`insert_balance_interrupt`, `repeat_last`, and `split_last_conjunction`.
They are opt-in because a mutation changes the turn sequence and must be
evaluated against the scenario's state contract.

## What a scenario asserts

Scenarios should assert canonical state and effects rather than exact wording:

- task types, stages and required-field presence;
- pending interrupt kind and scope;
- query-frame and clarification preservation;
- route directive and LLM-call budget;
- money-moving job counts and forbidden topics;
- grounded response facts and forbidden claims.

`ReadinessStateInvariant` addresses a privacy-safe state snapshot. Its
`preserve` mode compares the current turn with the previous turn, which is
useful for proving that a balance interruption did not discard a pending
transfer or query frame. Snapshots intentionally omit identifiers, amounts,
references and user text.

Collection invariants also support `contains` and `not_contains`, allowing a
scenario to assert that a mixed batch retained both task domains without
asserting private task IDs or payload values.

## Adding adversarial cases

Start with a small semantic seed, then add a mutation only when its expected
state transition is explicit. Utterance mutations (case, punctuation, typo,
filler and code-switching) are generally meaning-preserving. Conversation
mutations (repeat, split, interruption and cancellation) are opt-in because
their effects depend on the active state.

Every production failure should become a sanitized replay case containing:

1. a deterministic fixture profile;
2. the smallest reproducing turn sequence;
3. expected state/effect invariants;
4. a criticality and failure category;
5. a reproducible mutation or fault seed.

Financial correctness and safety remain deterministic. An optional quality
judge may score clarity and repetition, but it cannot approve execution or
override a state/effect failure.
