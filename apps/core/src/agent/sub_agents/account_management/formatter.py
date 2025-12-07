from typing import List, Any
from shared.formatters.accounts import get_last4, get_bank_label

class AccountManagementFormatter:
    @staticmethod
    def format_account_list(accounts: List[Any]) -> str:
        """Format list of accounts for display."""
        if not accounts:
            return (
                "You don't have any linked bank accounts yet.\n\n"
                "To link an account, I'll need to guide you through Mono Connect. "
                "This is currently done during onboarding, but we can set it up for you again."
            )
        
        lines = ["🏦 *Your Linked Accounts*\n"]
        for i, account in enumerate(accounts, 1):
            bank_name = get_bank_label(account)
            last4 = get_last4(account)
            
            if isinstance(account, dict):
                is_default = account.get("is_default", False)
                account_name = account.get("account_name", "Account")
            else:
                is_default = getattr(account, "is_default", False)
                account_name = getattr(account, "account_name", "Account") or "Account"
            
            default_badge = " ⭐ *Default*" if is_default else ""
            
            lines.append(f"Account {i}{default_badge}")
            lines.append(f"Bank: {bank_name}")
            lines.append(f"Number: ***{last4}")
            lines.append(f"Name: {account_name}")
            if i < len(accounts):
                lines.append("") 
        lines.append(
            "\n💡 *Quick Actions:*\n"
            "• Set default: Reply with account number (1, 2, etc.)\n"
            "• Unlink: Say 'unlink account [number]'\n"
            "• Add new: Say 'link new account'"
        )
        
        return "\n".join(lines)

