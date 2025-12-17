from typing import List, Any
from shared.formatters.accounts import get_last4, get_bank_label


STATUS_ICONS = {
    "ready": "✅",
    "pending": "⏳",
    "awaiting_authorization": "⏳",
    "expired": "⚠️",
    "cancelled": "⚠️",
    None: "",
}


class AccountManagementFormatter:
    @staticmethod
    def format_account_list(accounts: List[Any]) -> str:
        """Format list of accounts for display."""
        if not accounts:
            return (
                "You don't have any linked bank accounts yet.\n\n"
                "Say 'link account' to connect your bank."
            )
        
        lines = ["🏦 *Your Bank Accounts*\n"]
        
        for i, account in enumerate(accounts, 1):
            bank_name = get_bank_label(account)
            last4 = get_last4(account)
            
            if isinstance(account, dict):
                is_default = account.get("is_default", False)
                mandate_status = account.get("mandate_status")
            else:
                is_default = getattr(account, "is_default", False)
                mandate_status = getattr(account, "mandate_status", None)
            
            default_badge = " ⭐" if is_default else ""
            status_icon = STATUS_ICONS.get(mandate_status, "")
            
            lines.append(f"{i}. {bank_name} (****{last4}){default_badge} {status_icon}".rstrip())
        
        lines.append("")
        lines.append("_✅=ready ⏳=pending ⚠️=action needed_")
        lines.append("")
        lines.append("\"set 2 as default\" | \"unlink GTB\"")
        
        return "\n".join(lines)



