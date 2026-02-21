"""Account capability definitions."""

from enum import Enum

from shared.policy import resolve_capability_alternative, resolve_capability_message, resolve_capability_rule


class AccountCapability(str, Enum):
    """Capabilities for account."""

    LIST_ACCOUNTS = "list_accounts"
    LINK_ACCOUNT = "link_account"
    UNLINK_ACCOUNT = "unlink_account"
    SET_DEFAULT = "set_default"
    CLOSE_ACCOUNT = "close_account"
    CHANGE_BVN = "change_bvn"
    ADD_JOINT_HOLDER = "add_joint_holder"


CAPABILITY_LABELS: dict[AccountCapability, str] = {
    AccountCapability.LIST_ACCOUNTS: "list accounts",
    AccountCapability.LINK_ACCOUNT: "link new account",
    AccountCapability.UNLINK_ACCOUNT: "unlink account",
    AccountCapability.SET_DEFAULT: "set default account",
    AccountCapability.CLOSE_ACCOUNT: "close bank account",
    AccountCapability.CHANGE_BVN: "change BVN",
    AccountCapability.ADD_JOINT_HOLDER: "add joint account holder",
}


def check_capabilities(requires: list[AccountCapability]) -> list[AccountCapability]:
    """Check which required capabilities are missing.

    Policy is authoritative: if an action has no rule, treat it as unsupported.
    """
    missing: list[AccountCapability] = []
    for cap in requires:
        policy_rule = resolve_capability_rule(domain="account", action=cap.value)
        if policy_rule is None or not policy_rule.supported:
            missing.append(cap)
    return missing


def derive_requirements(user_message: str) -> list[AccountCapability]:
    """Derive required capabilities from user message."""
    requires: list[AccountCapability] = []
    msg_lower = user_message.lower()

    close_keywords = ["close account", "close my account", "delete account", "terminate account"]
    if any(kw in msg_lower for kw in close_keywords):
        requires.append(AccountCapability.CLOSE_ACCOUNT)

    bvn_keywords = ["change bvn", "update bvn", "new bvn", "wrong bvn"]
    if any(kw in msg_lower for kw in bvn_keywords):
        requires.append(AccountCapability.CHANGE_BVN)

    joint_keywords = ["joint account", "add someone", "add holder", "shared account"]
    if any(kw in msg_lower for kw in joint_keywords):
        requires.append(AccountCapability.ADD_JOINT_HOLDER)

    return list(set(requires))


def generate_limitation_message(missing: list[AccountCapability]) -> str:
    """Generate user-friendly limitation message."""
    if not missing:
        return ""

    first = missing[0]
    if policy_message := resolve_capability_message(domain="account", action=first.value):
        return policy_message

    if alt := resolve_capability_alternative(domain="account", action=first.value):
        return f"*{CAPABILITY_LABELS.get(first, first.value).title()}* isn't available yet. I can help with *{alt}*."

    missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
    return f"*{missing_labels[0].title()}* isn't available yet."
