"""Policy validation helpers for runtime safety."""

from __future__ import annotations

from shared.policy.models import CapabilityPolicy

REQUIRED_DOMAIN_ACTIONS: dict[str, set[str]] = {
    "transfer": {
        "send_money",
    },
    "airtime": {
        "buy_airtime",
    },
    "data": {
        "buy_data",
    },
    "account": {
        "list_accounts",
        "link_account",
        "unlink_account",
        "set_default",
        "close_account",
        "change_bvn",
        "add_joint_holder",
    },
    "support": {
        "lookup_transaction",
        "explain_status",
        "retry_payout",
        "initiate_refund",
        "queue_refund_request",
        "collect_details",
        "create_ticket",
        "escalate",
    },
    "query": {
        "filter_recipient",
        "filter_amount",
        "filter_category",
        "filter_tx_type",
        "filter_bank",
        "search_narration_keyword",
        "search_narration_fuzzy",
        "time_relative",
        "time_all",
        "aggregate_sum",
        "aggregate_group",
        "time_comparison",
        "export_pdf",
        "export_csv",
    },
}


def validate_policy_coverage(policy: CapabilityPolicy) -> None:
    """Validate required policy sections and action coverage.

    Raises:
        ValueError: if required sections are missing or inconsistent.
    """
    errors: list[str] = []

    for domain, required_actions in REQUIRED_DOMAIN_ACTIONS.items():
        domain_policy = policy.capability_matrix.get(domain)
        if not domain_policy:
            errors.append(f"capability_matrix.{domain} is missing")
            continue
        missing_actions = sorted(required_actions - set(domain_policy.actions.keys()))
        if missing_actions:
            errors.append(f"capability_matrix.{domain}.actions missing: {', '.join(missing_actions)}")
            continue

        for action_name, rule in domain_policy.actions.items():
            if not rule.alternative:
                continue
            alt_rule = domain_policy.actions.get(rule.alternative)
            if alt_rule is None:
                errors.append(
                    f"capability_matrix.{domain}.actions.{action_name} alternative '{rule.alternative}' is missing"
                )
                continue
            if not alt_rule.supported:
                errors.append(
                    "capability_matrix."
                    f"{domain}.actions.{action_name} alternative '{rule.alternative}' must be supported"
                )

    if errors:
        raise ValueError("Invalid soul policy: " + " | ".join(errors))
