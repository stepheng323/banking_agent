# Runtime Policy Authoring Guide

This guide explains how to write and validate the split runtime policy for the banking agent.

## Canonical Source

- Assistant profile file: `config/assistant_profile.json`
- Capability policy file: `config/capability_policy.json`
- Domain guardrails file: `config/domain_guardrails.json`
- Config overrides:
  - `ASSISTANT_PROFILE_PATH` (defaults to `config/assistant_profile.json`)
  - `CAPABILITY_POLICY_PATH` (defaults to `config/capability_policy.json`)
  - `DOMAIN_GUARDRAILS_PATH` (defaults to `config/domain_guardrails.json`)
- Runtime behavior: JSON only, fail-fast on missing or invalid config
- `soul.md` is narrative only and is not parsed at runtime
- `SOUL_POLICY_PATH` is legacy compatibility only and is no longer the runtime source of truth

## File Responsibilities

### `config/assistant_profile.json`

- `version`: string
- `last_updated`: string or `null`
- `identity`: object
- `tone`: object
- `safety_rules`: string array

### `config/capability_policy.json`

- `version`: string
- `last_updated`: string or `null`
- `capability_matrix`: object mapping domain name to domain capability policy

### `config/domain_guardrails.json`

- `version`: string
- `last_updated`: string or `null`
- `transfer`: object containing transfer safety/consistency controls
  - `relational_aliases`: string array for expected colloquial names ("dad", "mum")
  - `name_match.min_similarity`: float threshold for mismatch warning
  - `dynamic_risk.floor_amount`: numeric floor for high-risk warning
  - `dynamic_risk.lookback_days`: integer lookback window
  - `dynamic_risk.percentile`: float percentile used for per-user threshold
- `query`: object for query/runtime limits
- `support`: object for support/runtime thresholds

## Capability Matrix Contract

Each domain inside `capability_matrix` must contain:

- `domain`: string name
- `actions`: object mapping `action_name -> rule`

Each action rule supports:

- `supported`: boolean (default `true` if omitted in typed model, but keep explicit in JSON)
- `alternative`: string or `null` (optional)
- `limitation_message`: string or `null` (optional)

## Required Domain Actions

Coverage is validated at startup/tests by `validate_policy_coverage`.

- `transfer`: `send_money`
- `airtime`: `buy_airtime`
- `data`: `buy_data`
- `account`: `list_accounts`, `link_account`, `unlink_account`, `set_default`, `close_account`, `change_bvn`, `add_joint_holder`
- `support`: `lookup_transaction`, `explain_status`, `retry_payout`, `initiate_refund`, `queue_refund_request`, `collect_details`, `create_ticket`, `escalate`
- `query`: `filter_recipient`, `filter_amount`, `filter_category`, `filter_tx_type`, `filter_bank`, `search_narration_keyword`, `search_narration_fuzzy`, `time_relative`, `time_all`, `aggregate_sum`, `aggregate_group`, `time_comparison`, `export_pdf`, `export_csv`

## Consistency Rules

- Every required action must exist in the domain matrix.
- If an action declares an `alternative`, that alternative must exist in the same domain.
- Any `alternative` target must point to a supported action.
- Use action names already consumed by workers/capability checks; renaming actions is a runtime behavior change.

## Authoring Workflow

1. Edit the relevant config file:
   - `config/assistant_profile.json`
   - `config/capability_policy.json`
   - `config/domain_guardrails.json`
2. Validate schema + coverage:

```bash
uv run python -c "from shared.assistant_profile import load_assistant_profile; from shared.policy import load_policy, validate_policy_coverage; from shared.guardrails import load_guardrails; profile = load_assistant_profile('config/assistant_profile.json'); policy = load_policy('config/capability_policy.json'); guardrails = load_guardrails('config/domain_guardrails.json'); validate_policy_coverage(policy); print('assistant profile ok:', profile.version); print('capability policy ok:', policy.version); print('guardrails ok:', guardrails.version)"
```

3. Run policy tests:

```bash
uv run pytest tests/shared/test_soul_policy.py
```

## Common Failure Cases

- Missing config file: startup/test fails immediately.
- Malformed JSON: loader raises `Invalid JSON in policy file ...`.
- Missing required action in a domain: coverage validation fails.
- Invalid alternative target: coverage validation fails.

## Editing Guidance for Limitation Messages

- State clearly what cannot be done.
- Redirect immediately to what can be done.
- Keep it short and operational.
- Prefer explicit CTA language, for example: "Want me to do that now?"
