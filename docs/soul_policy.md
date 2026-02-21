# Soul Policy Authoring Guide

This guide explains how to write a valid runtime policy for the banking agent.

## Canonical Source

- Runtime policy file: `config/soul_policy.json`
- Config override: `SOUL_POLICY_PATH` (defaults to `config/soul_policy.json`)
- Runtime behavior: JSON only, fail-fast on missing or invalid policy
- `soul.md` is narrative only and is not parsed at runtime

## Required Top-Level Shape

`config/soul_policy.json` must be a JSON object with these fields:

- `version`: string
- `last_updated`: string or `null`
- `identity`: object
- `tone`: object
- `supported_domains`: string array
- `unsupported_capabilities`: string array
- `unsupported_detection`: object mapping capability name to phrase array
- `unsupported_alternatives`: object mapping capability name to alternative array
- `safety_rules`: string array
- `capability_matrix`: object mapping domain name to domain capability policy

## Identity and Tone

- `identity.name`: product/assistant name
- `identity.description`: one-line functional description
- `identity.positioning`: hard boundary statement
- `tone.style`: high-level writing style
- `tone.brevity`: expected response length guidance
- `tone.response_rules`: deterministic rules the assistant should follow

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

- Every key in `unsupported_detection` must exist in `unsupported_capabilities`.
- Every key in `unsupported_alternatives` must exist in `unsupported_capabilities`.
- Use action names already consumed by workers/capability checks; renaming actions is a runtime behavior change.

## Authoring Workflow

1. Edit `config/soul_policy.json`.
2. Validate schema + coverage:

```bash
uv run python -c "from shared.policy import load_policy, validate_policy_coverage; p = load_policy('config/soul_policy.json'); validate_policy_coverage(p); print('policy ok:', p.version)"
```

3. Run policy tests:

```bash
uv run pytest tests/shared/test_soul_policy.py
```

## Common Failure Cases

- Missing policy file: startup/test fails immediately.
- Malformed JSON: loader raises `Invalid JSON in policy file ...`.
- Missing required action in a domain: coverage validation fails.
- Unknown capability name under `unsupported_detection` or `unsupported_alternatives`: coverage validation fails.

## Editing Guidance for Limitation Messages

- State clearly what cannot be done.
- Redirect immediately to what can be done.
- Keep it short and operational.
- Prefer explicit CTA language, for example: "Want me to do that now?"
