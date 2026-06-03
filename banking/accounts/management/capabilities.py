"""Account capability definitions."""

from enum import Enum

from banking.policy.adapters import check_unsupported_actions, resolve_capability_alternative
from banking.presentation.i18n.bridge import render_capability_limitation


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
    missing = check_unsupported_actions(domain="account", requested_actions=[cap.value for cap in requires])
    return [cap for cap in requires if cap.value in missing]


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


def generate_limitation_message(missing: list[AccountCapability], *, locale: str = "en") -> str:
    """Generate user-friendly limitation message."""
    if not missing:
        return ""

    first = missing[0]
    alt = resolve_capability_alternative(domain="account", action=first.value)
    alt_label = alt.replace("_", " ") if alt else None

    return render_capability_limitation(
        locale=locale,
        action_label=CAPABILITY_LABELS.get(first, first.value),
        alternative_labels=[alt_label] if alt_label else [],
    )
