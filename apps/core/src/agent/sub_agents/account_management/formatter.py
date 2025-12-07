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
        
        lines = ["🏦 *Your Linked Accounts:*\n"]
        for i, account in enumerate(accounts, 1):
            bank_name = get_bank_label(account)
            last4 = get_last4(account)
            
            # Handle is_default and account_name
            if isinstance(account, dict):
                is_default = account.get("is_default", False)
                account_name = account.get("account_name", "Account")
            else:
                is_default = getattr(account, "is_default", False)
                account_name = getattr(account, "account_name", "Account") or "Account"
            
            default_marker = " ✓ *Default*" if is_default else ""
            
            lines.append(f"{i}. {bank_name} (***{last4}){default_marker}\n   {account_name}")
            
        lines.append(
            "\n\n💡 *Tips:*\n"
            "• Reply with a number (1, 2, etc.) to set that as your default account\n"
            "• Say 'unlink account [number]' to remove an account\n"
            "• Say 'link new account' to add another account"
        )
        
        return "\n".join(lines)

