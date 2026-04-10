"""Account management worker (stateless)."""

import asyncio
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
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.i18n import LocaleManager, render_message
from shared.repositories.account_repository import AccountRepository
from shared.repositories.unit_of_work import UnitOfWork
from shared.repositories.user_repository import UserRepository
from shared.services.onboarding import SessionManager
from shared.utils.logging import get_logger

logger = get_logger(__name__)

ACTION_CAPABILITY_MAP: dict[str, AccountCapability] = {
    "list": AccountCapability.LIST_ACCOUNTS,
    "list_accounts": AccountCapability.LIST_ACCOUNTS,
    "count": AccountCapability.LIST_ACCOUNTS,
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
        banking_provider: BankDataProvider,
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
        del pin_verified
        text = (user_message or "").strip()
        patch: dict[str, Any] = {}

        user_ctx = {
            "profile": context.get("profile"),
            "accounts": context.get("accounts", []),
            "language": context.get("language"),
        }
        locale = self._resolve_locale(user_ctx, payload)

        if text:
            missing_caps = check_capabilities(derive_requirements(text))
            if missing_caps:
                logger.info("capability_blocked", domain="account", capabilities=[cap.value for cap in missing_caps])
                response = generate_limitation_message(missing_caps, locale=locale)
                response = await self._translate_if_needed(response, user_ctx, payload)
                return AccountResult(outcome=AccountOutcome.OK, response=response)

        action = payload.get("action")
        identifier = payload.get("identifier")
        identifiers = payload.get("identifiers")

        if not action:
            parsed = await self.parser.parse(text)
            action = parsed.action
            identifier = identifier or parsed.identifier
            identifiers = parsed.identifiers
            patch["action"] = action
            if identifier:
                patch["identifier"] = identifier
            if identifiers:
                patch["identifiers"] = identifiers
            if parsed.language:
                patch["language"] = parsed.language
        elif action in ("unlink", "set_default") and not identifier and text:
            parsed = await self.parser.parse(text)
            identifier = parsed.identifier or text
            patch["identifier"] = identifier
        elif action in ("check_balance", "balance", "show_balance", "overall_balance") and not identifier and not identifiers and text:
            parsed = await self.parser.parse(text)
            identifier = parsed.identifier
            identifiers = parsed.identifiers
            if identifier:
                patch["identifier"] = identifier
            if identifiers:
                patch["identifiers"] = identifiers
        elif action in ("list", "list_accounts") and text:
            # Let parser refine list-like intents into nuanced account intents (e.g. count).
            parsed = await self.parser.parse(text)
            if parsed.action and parsed.action != "unknown":
                action = parsed.action
                patch["action"] = action
            if not identifier and parsed.identifier:
                identifier = parsed.identifier
                patch["identifier"] = identifier
            if parsed.language:
                patch["language"] = parsed.language

        if action == "list_accounts":
            action = "list"
            patch["action"] = action

        if action == "unknown" or not action:
            action = "list"
            patch["action"] = action

        capability = ACTION_CAPABILITY_MAP.get(str(action))
        if capability:
            missing_caps = check_capabilities([capability])
            if missing_caps:
                logger.info("capability_blocked", domain="account", capabilities=[cap.value for cap in missing_caps])
                response = generate_limitation_message(missing_caps, locale=locale)
                response = await self._translate_if_needed(response, user_ctx, payload)
                return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        if action in ("unlink", "set_default") and not identifier:
            prompt = self._missing_identifier_prompt(action, locale)
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
            response = render_message("account.user_not_found", locale)
            response = await self._translate_if_needed(response, user_ctx, payload)
            return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        if action == "count":
            accounts = user_ctx.get("accounts") or []
            if not accounts and user_id:
                accounts = await self.account_repo.get_by_user(user_id)
            count = len(accounts)
            noun = "account" if count == 1 else "accounts"
            response = f"You have {count} linked {noun}."
            response = await self._translate_if_needed(response, user_ctx, payload)
            return AccountResult(
                outcome=AccountOutcome.OK,
                response=response,
                patch=patch,
            )

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
                response = await self._set_default(user_id, str(identifier), locale=locale)
            elif action == "unlink":
                response = await self._unlink_account(user_id, str(identifier), locale=locale)
            elif action in ("check_balance", "balance", "show_balance", "overall_balance"):
                account_identifiers = identifiers or ([str(identifier)] if identifier else None)
                response = await self._check_balance(user_id, account_identifiers, locale=locale)
            else:
                accounts = user_ctx.get("accounts")
                if accounts:
                    response = AccountFormatter.format_account_list(accounts, locale=locale)
                else:
                    response = await self._list_accounts(user_id, locale=locale)

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
                error=render_message("account.error.status_check_failed", locale),
                patch=patch,
            )

    async def _check_balance(
        self, user_id: str, account_identifiers: list[str] | None, *, locale: str = "en"
    ) -> str:
        """Check balance for specific account(s) or all accounts."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return render_message("account.no_linked_accounts", locale)

        target_accounts = []
        if account_identifiers:
            for ident in account_identifiers:
                try:
                    idx = int(ident)
                    if 1 <= idx <= len(accounts):
                        target_accounts.append(accounts[idx - 1])
                except ValueError:
                    found = self._find_account_by_bank_name(accounts, ident)
                    if found:
                        target_accounts.append(found)

            if not target_accounts:
                label = ", ".join(account_identifiers)
                return render_message(
                    "account.account_not_found",
                    locale,
                    {"identifier": label},
                )
        else:
            target_accounts = accounts

        balances = []
        total_balance = 0.0

        for account in target_accounts:
            try:
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
            return render_message("account.balance.unavailable", locale)

        return AccountFormatter.format_balance_response(
            balances,
            total_balance if len(balances) > 1 else None,
            locale=locale,
        )

    def _missing_identifier_prompt(self, action: str, locale: str = "en") -> str:
        if action == "unlink":
            return render_message("account.prompt.unlink_identifier", locale)
        if action == "set_default":
            return render_message("account.prompt.default_identifier", locale)
        return render_message("account.prompt.which_account", locale)

    async def _translate_if_needed(
        self,
        text: str,
        user_ctx: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        del user_ctx, payload
        return text

    @staticmethod
    def _resolve_locale(user_ctx: dict[str, Any], payload: dict[str, Any]) -> str:
        language = user_ctx.get("language") or payload.get("language")
        return LocaleManager.normalize(language).value

    async def _list_accounts(self, user_id: str, *, locale: str = "en") -> str:
        """List all linked accounts for a user."""
        accounts = await self.account_repo.get_by_user(user_id)
        return AccountFormatter.format_account_list(accounts, locale=locale)

    async def _set_default(self, user_id: str, account_identifier: str, *, locale: str = "en") -> str:
        """Set an account as default."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return render_message("account.no_linked_accounts", locale)

        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)

        if not selected_account:
            return render_message(
                "account.account_not_found_with_count",
                locale,
                {
                    "identifier": account_identifier,
                    "count": len(accounts),
                },
            )

        try:
            async with UnitOfWork() as uow:
                await uow.accounts.set_default_account(user_id, str(selected_account.account_id))
                await uow.commit()

            user = await self.user_repo.get_by_id(user_id)
            if user:
                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))

            masked = f"***{selected_account.account_number[-4:]}"
            return render_message(
                "account.default_updated",
                locale,
                {
                    "bank_name": selected_account.bank_name,
                    "masked": masked,
                },
            )
        except Exception as e:
            logger.error(f"set_default_error: {e}")
            return render_message("account.error.default_update_failed", locale)

    async def _unlink_account(self, user_id: str, account_identifier: str, *, locale: str = "en") -> str:
        """Unlink an account."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return render_message("account.no_linked_accounts", locale)
        if len(accounts) == 1:
            return render_message("account.unlink.only_account", locale)

        selected_account = None
        try:
            account_index = int(account_identifier)
            if 1 <= account_index <= len(accounts):
                selected_account = accounts[account_index - 1]
        except ValueError:
            selected_account = self._find_account_by_bank_name(accounts, account_identifier)

        if not selected_account:
            return render_message(
                "account.account_not_found",
                locale,
                {"identifier": account_identifier},
            )

        try:
            if getattr(selected_account, "mandate_id", None):
                try:
                    await self.direct_debit_provider.cancel_mandate(selected_account.mandate_id)
                except Exception:
                    pass

            async with UnitOfWork() as uow:
                success = await uow.accounts.delete_account(str(selected_account.account_id), user_id)
                await uow.commit()
            if success:
                try:
                    async with UnitOfWork() as uow:
                        if uow.users:
                            user = await uow.users.get_by_id(user_id)
                            if user:
                                asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))
                except Exception:
                    pass

                masked = f"***{selected_account.account_number[-4:]}"
                return render_message(
                    "account.unlink.success",
                    locale,
                    {
                        "bank_name": selected_account.bank_name,
                        "masked": masked,
                    },
                )
            return render_message("account.error.unlink_failed", locale)
        except Exception as e:
            logger.error(f"unlink_error: {e}")
            return render_message("account.error.unlink_failed", locale)

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

        from shared.config.settings import settings
        from shared.services.onboarding.session import OnboardingStep

        flow_id = settings.account_linking_flow_id
        locale = LocaleManager.normalize(context.get("language")).value
        if not flow_id:
            return {"error": render_message("account.linking.unavailable", locale)}

        phone_number = context.get("phone_number", "")
        profile = context.get("profile") or {}
        canonical_phone_number = str(profile.get("phone_number") or phone_number or "").strip()
        bvn = (profile.get("extra_data") or {}).get("bvn")

        if not bvn:
            return {"error": render_message("account.linking.bvn_missing", locale)}

        result = await self.banking_provider.initiate_bvn_lookup(bvn)
        if not result.success:
            return {"error": result.error_message or render_message("account.linking.start_failed", locale)}

        methods = [{"id": m["method"], "title": m["hint"]} for m in result.verification_methods]
        flow_token_phone = canonical_phone_number or str(phone_number or "").strip()
        flow_token = f"link-{flow_token_phone}-{int(time.time())}"
        session_payload = {
            "phone_number": canonical_phone_number,
            "bvn": bvn,
            "session_id": result.session_id,
            "methods": methods,
            "step": OnboardingStep.METHOD_SELECTION.value,
            "is_account_linking": True,
            "channel": context.get("channel", "whatsapp"),
        }
        if not self.session_manager:
            logger.error("account_linking_session_manager_missing", flow_token=flow_token)
            return {"error": render_message("account.linking.start_failed", locale)}

        stored = await self.session_manager.update_session_strict(
            flow_token,
            session_payload,
            verify=True,
        )
        if not stored:
            logger.error(
                "account_linking_session_create_failed",
                flow_token=flow_token,
                phone=canonical_phone_number,
                channel=context.get("channel", "unknown"),
            )
            return {"error": render_message("account.linking.start_failed", locale)}

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
            "fallback_text": render_message(
                "account.linking.fallback_link",
                locale,
                {"flow_token": flow_token},
            ),
        }
