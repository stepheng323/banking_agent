"""Account management worker (stateless)."""

import time
from typing import Any

from langchain_core.language_models import BaseChatModel

from apps.core.src.agent.graphs.account.capabilities import (
    AccountCapability,
    check_capabilities,
    derive_requirements,
    generate_limitation_message,
)
from apps.core.src.agent.graphs.account.formatter import AccountFormatter
from apps.core.src.agent.graphs.account.parser import AccountParser
from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    AccountResult,
)
from shared.clients.abstractions.banking import BankingDataProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.repositories.account_repository import AccountRepository
from shared.repositories.user_repository import UserRepository
from shared.services.onboarding import SessionManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

ACTION_CAPABILITY_MAP: dict[str, AccountCapability] = {
    "list": AccountCapability.LIST_ACCOUNTS,
    "check_balance": AccountCapability.LIST_ACCOUNTS,
    "show_balance": AccountCapability.LIST_ACCOUNTS,
    "balance": AccountCapability.LIST_ACCOUNTS,
    "overall_balance": AccountCapability.LIST_ACCOUNTS,
    "set_default": AccountCapability.SET_DEFAULT,
    "unlink": AccountCapability.UNLINK_ACCOUNT,
    "link": AccountCapability.LINK_ACCOUNT,
    "close_account": AccountCapability.CLOSE_ACCOUNT,
    "change_bvn": AccountCapability.CHANGE_BVN,
    "add_joint_holder": AccountCapability.ADD_JOINT_HOLDER,
}


class AccountWorker:
    """Stateless worker for account tasks."""

    def __init__(
        self,
        account_repo: AccountRepository,
        user_repo: UserRepository,
        llm: BaseChatModel,
        banking_provider: BankingDataProvider,
        session_manager: SessionManager,
        direct_debit_provider: DirectDebitProvider,
    ) -> None:
        self.account_repo = account_repo
        self.user_repo = user_repo
        self.llm = llm
        self.banking_provider = banking_provider
        self.session_manager = session_manager
        self.parser = AccountParser(llm)
        self.direct_debit_provider = direct_debit_provider

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> AccountResult:
        """Execute account management logic and return a structured result."""
        text = (user_message or "").strip()
        patch: dict[str, Any] = {}

        user_ctx = {
            "profile": context.get("profile"),
            "accounts": context.get("accounts", []),
            "language": context.get("language"),
        }

        if text:
            missing_caps = check_capabilities(derive_requirements(text))
            if missing_caps:
                logger.info("capability_blocked", domain="account", capabilities=[cap.value for cap in missing_caps])
                response = generate_limitation_message(missing_caps)
                response = await self._translate_if_needed(response, user_ctx, payload)
                return AccountResult(outcome=AccountOutcome.OK, response=response)

        action = payload.get("action")
        identifier = payload.get("identifier")

        if not action:
            parsed = await self.parser.parse(text)
            action = parsed.action
            identifier = identifier or parsed.identifier
            patch["action"] = action
            if identifier:
                patch["identifier"] = identifier
            if parsed.language:
                patch["language"] = parsed.language
        elif action in ("unlink", "set_default") and not identifier and text:
            parsed = await self.parser.parse(text)
            identifier = parsed.identifier or text
            patch["identifier"] = identifier

        if action == "unknown" or not action:
            action = "list"
            patch["action"] = action

        capability = ACTION_CAPABILITY_MAP.get(str(action))
        if capability:
            missing_caps = check_capabilities([capability])
            if missing_caps:
                logger.info("capability_blocked", domain="account", capabilities=[cap.value for cap in missing_caps])
                response = generate_limitation_message(missing_caps)
                response = await self._translate_if_needed(response, user_ctx, payload)
                return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        if action in ("unlink", "set_default") and not identifier:
            prompt = self._missing_identifier_prompt(action)
            prompt = await self._translate_if_needed(prompt, user_ctx, payload)
            return AccountResult(
                outcome=AccountOutcome.NEEDS_INPUT,
                required_fields=["identifier"],
                prompt=prompt,
                patch=patch,
            )

        profile = user_ctx.get("profile") or {}
        user_id = str(profile.get("id") or context.get("user_id") or "")
        if not user_id and action != "link":
            response = "User not found."
            response = await self._translate_if_needed(response, user_ctx, payload)
            return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        try:
            if action == "link":
                flow_data = await self._build_link_account_flow(context)
                if flow_data.get("error"):
                    response = flow_data["error"]
                else:
                    return AccountResult(
                        outcome=AccountOutcome.OK,
                        patch=patch,
                        outbox=[
                            {
                                "type": "flow",
                                "flow_id": flow_data["flow_id"],
                                "flow_config": flow_data["flow_config"],
                                "fallback_text": flow_data.get("fallback_text", ""),
                            }
                        ],
                    )
            elif action == "set_default":
                response = await self._set_default(user_id, str(identifier))
            elif action == "unlink":
                response = await self._unlink_account(user_id, str(identifier))
            elif action in ("check_balance", "balance", "show_balance", "overall_balance"):
                response = await self._check_balance(user_id, str(identifier) if identifier else None)
            else:
                accounts = user_ctx.get("accounts")
                if accounts:
                    response = AccountFormatter.format_account_list(accounts)
                else:
                    response = await self._list_accounts(user_id)

            response = await self._translate_if_needed(response, user_ctx, payload)
            return AccountResult(
                outcome=AccountOutcome.OK,
                response=response,
                patch=patch,
            )
        except Exception as e:
            logger.error("account_worker_failed", error=str(e), exc_info=True)
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error="Account status check failed. Please try again.",
                patch=patch,
            )

    async def _check_balance(self, user_id: str, account_identifier: str | None) -> str:
        """Check balance for one or all accounts."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return "You don't have any linked accounts."

        target_accounts = []
        if account_identifier:
            # Find specific account
            try:
                idx = int(account_identifier)
                if 1 <= idx <= len(accounts):
                    target_accounts = [accounts[idx - 1]]
            except ValueError:
                found = self._find_account_by_bank_name(accounts, account_identifier)
                if found:
                    target_accounts = [found]
        else:
            target_accounts = accounts

        if not target_accounts:
            return f"I couldn't find an account matching '{account_identifier}'."

        balances = []
        total_balance = 0.0

        for account in target_accounts:
            try:
                # Use provider to get real-time balance
                bal_data = await self.banking_provider.get_balance(account.account_id)
                if bal_data:
                    balances.append(
                        {
                            "bank_name": account.bank_name,
                            "account_number": account.account_number,
                            "amount": bal_data.available_balance,
                            "currency": bal_data.currency,
                        }
                    )
                    total_balance += bal_data.available_balance
            except Exception as e:
                logger.error("balance_fetch_failed", error=str(e))

        if not balances:
            return "I couldn't retrieve your balance at the moment."

        return AccountFormatter.format_balance_response(balances, total_balance if len(balances) > 1 else None)

    def _missing_identifier_prompt(self, action: str) -> str:
        if action == "unlink":
            return "Which account would you like to unlink? Say 'unlink [bank name]' or 'unlink [number]'."
        if action == "set_default":
            return "Which account should be your default? Say 'set [bank name] as default'."
        return "Which account?"

    async def _translate_if_needed(
        self,
        text: str,
        user_ctx: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        language = user_ctx.get("language") or payload.get("language")
        if language and language.lower() not in ("english", "en"):
            return await self._translate_response(text, language)
        return text

    async def _list_accounts(self, user_id: str) -> str:
        """List all linked accounts for a user."""
        accounts = await self.account_repo.get_by_user(user_id)
        return AccountFormatter.format_account_list(accounts)

    async def _set_default(self, user_id: str, account_identifier: str) -> str:
        """Set an account as default."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return "You don't have any linked accounts."

        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)

        if not selected_account:
            return (
                f"I couldn't find an account matching '{account_identifier}'.\n\n"
                f"You have {len(accounts)} linked account(s)."
            )

        try:
            from shared.cache.user_data import UserDataCache
            from shared.repositories.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                await uow.accounts.set_default_account(user_id, str(selected_account.account_id))
                await uow.commit()

            user = await self.user_repo.get_by_id(user_id)
            if user:
                import asyncio

                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))

            masked = f"***{selected_account.account_number[-4:]}"
            return f"✓ *Default account updated!*\n\n{selected_account.bank_name} ({masked}) is now your default."
        except Exception as e:
            logger.error(f"set_default_error: {e}")
            return "Sorry, I couldn't update your default account."

    async def _unlink_account(self, user_id: str, account_identifier: str) -> str:
        """Unlink an account."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return "You don't have any linked accounts."
        if len(accounts) == 1:
            return "⚠️ You can't unlink your only account. Link another one first."

        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)

        if not selected_account:
            return f"I couldn't find an account matching '{account_identifier}'."

        try:
            if getattr(selected_account, "mandate_id", None):
                try:
                    await self.direct_debit_provider.cancel_mandate(selected_account.mandate_id)
                except Exception:
                    pass

            success = await self.account_repo.delete_account(str(selected_account.account_id), user_id)
            if success:
                from shared.cache.user_data import UserDataCache
                from shared.repositories.unit_of_work import UnitOfWork

                try:
                    async with UnitOfWork() as uow:
                        if uow.users:
                            user = await uow.users.get_by_id(user_id)
                            if user:
                                import asyncio

                                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))
                except Exception:
                    pass

                masked = f"***{selected_account.account_number[-4:]}"
                return f"✓ *Account unlinked!*\n\n{selected_account.bank_name} ({masked}) removed."
            return "Sorry, I couldn't unlink that account."
        except Exception as e:
            logger.error(f"unlink_error: {e}")
            return "Sorry, I couldn't unlink that account."

    def _find_account_by_bank_name(self, accounts: list[Any], bank_name: str) -> Any | None:
        from shared.utils.bank_aliases import normalize_bank_name

        bank_name_lower = bank_name.lower().strip()
        normalized_search = normalize_bank_name(bank_name)
        for account in accounts:
            account_bank_lower = account.bank_name.lower()
            if (
                normalized_search in account_bank_lower
                or account_bank_lower in normalized_search
                or bank_name_lower in account_bank_lower
            ):
                return account
        return None

    async def _build_link_account_flow(self, context: dict[str, Any]) -> dict[str, Any]:
        """Build account linking flow."""

        from shared.config import settings
        from shared.services.onboarding.session import OnboardingStep

        flow_id = settings.account_linking_flow_id
        if not flow_id:
            return {"error": "Account linking unavailable."}

        phone_number = context.get("phone_number", "")
        profile = context.get("profile") or {}
        bvn = (profile.get("extra_data") or {}).get("bvn")

        if not bvn:
            return {"error": "BVN not found. Please complete onboarding first."}

        result = await self.banking_provider.initiate_bvn_lookup(bvn)
        if not result.success:
            return {"error": result.error_message or "Failed to start linking."}

        methods = [{"id": m["method"], "title": m["hint"]} for m in result.verification_methods]
        flow_token = f"link-{phone_number}-{int(time.time())}"

        await self.session_manager.update_session(
            flow_token,
            {
                "phone_number": phone_number,
                "bvn": bvn,
                "session_id": result.session_id,
                "methods": methods,
                "step": OnboardingStep.METHOD_SELECTION.value,
                "is_account_linking": True,
            },
        )

        return {
            "flow_id": flow_id,
            "flow_config": {
                "header": "Link New Account",
                "text_body": "Tap Continue to link a new bank account.",
                "flow_cta": "Link Account",
                "screen_name": "METHOD_SELECTION",
                "flow_token": flow_token,
                "flow_action_payload": {"screen": "METHOD_SELECTION", "data": {"methods": methods, "bvn": result.bvn}},
            },
            "fallback_text": f"Link account: https://fusepay.io/link/{flow_token}",
        }

    async def _translate_response(self, text: str, language: str) -> str:
        try:
            prompt = f"Translate to {language}. Keep formatting/emojis. Resp:\\n{text}"
            result = await self.llm.ainvoke(prompt)
            return getattr(result, "content", str(result))
        except Exception:
            return text
