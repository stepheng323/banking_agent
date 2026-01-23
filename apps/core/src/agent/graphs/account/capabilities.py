"""Account capability definitions.

Defines what the account graph supports and doesn't support.
"""

from enum import Enum


class AccountCapability(str, Enum):
    """Capabilities for account."""

    LIST_ACCOUNTS = "list_accounts"
    LINK_ACCOUNT = "link_account"
    UNLINK_ACCOUNT = "unlink_account"
    SET_DEFAULT = "set_default"
    CLOSE_ACCOUNT = "close_account"
    CHANGE_BVN = "change_bvn"
    ADD_JOINT_HOLDER = "add_joint_holder"


ACCOUNT_SUPPORTS: list[AccountCapability] = [
    AccountCapability.LIST_ACCOUNTS,
    AccountCapability.LINK_ACCOUNT,
    AccountCapability.UNLINK_ACCOUNT,
    AccountCapability.SET_DEFAULT,
]


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
    """Check which required capabilities are missing."""
    return [cap for cap in requires if cap not in ACCOUNT_SUPPORTS]


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

    if AccountCapability.CLOSE_ACCOUNT in missing:
        return (
            "I can't close bank accounts — that needs to be done directly with your bank.\n\n"
            "Would you like me to *unlink* an account from this app instead?"
        )

    if AccountCapability.CHANGE_BVN in missing:
        return (
            "BVN changes need to be done through your bank or NIBSS.\n\n"
            "I can only help with linking/unlinking accounts here."
        )

    if AccountCapability.ADD_JOINT_HOLDER in missing:
        return (
            "Adding joint account holders needs to be done through your bank.\n\n"
            "Is there something else I can help with?"
        )

    missing_labels = [CAPABILITY_LABELS.get(cap, cap.value) for cap in missing]
    return f"*{missing_labels[0].title()}* isn't available yet."
