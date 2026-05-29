"""Account management worker (stateless)."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from apps.chat.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    AccountResult,
)
from apps.chat.src.agent.workers.account.balances import check_balance
from apps.chat.src.agent.workers.account.capabilities import (
    AccountCapability,
    check_capabilities,
    derive_requirements,
    generate_limitation_message,
)
from apps.chat.src.agent.workers.account.formatter import AccountFormatter
from apps.chat.src.agent.workers.account.linking import build_link_account_flow
from apps.chat.src.agent.workers.account.mutations import missing_identifier_prompt, set_default_account, unlink_account
from apps.chat.src.agent.workers.account.parser import AccountParser
from apps.chat.src.agent.workers.account.serialization import serialize_accounts
from banking.accounts.repositories.account_repository import AccountRepository
from banking.identity.repositories.user_repository import UserRepository
from shared.cache.flow_session_manager import FlowSessionManager
from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
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
        session_manager: FlowSessionManager,
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
        elif (
            action in ("check_balance", "balance", "show_balance", "overall_balance")
            and not identifier
            and not identifiers
            and text
        ):
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
                return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        if action in ("unlink", "set_default") and not identifier:
            prompt = missing_identifier_prompt(action, locale)
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
            return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        if action == "count":
            accounts = user_ctx.get("accounts") or []
            if not accounts and user_id:
                accounts = await self.account_repo.get_by_user(user_id)
            count = len(accounts)
            noun = "account" if count == 1 else "accounts"
            response = f"You have {count} linked {noun}."
            return AccountResult(
                outcome=AccountOutcome.OK,
                response=response,
                patch=patch,
                details={"viewed_accounts": serialize_accounts(accounts)} if accounts else {},
            )

        viewed_accounts: list[dict[str, Any]] = []
        try:
            if action == "link":
                flow_data = await build_link_account_flow(
                    context=context,
                    banking_provider=self.banking_provider,
                    session_manager=self.session_manager,
                )
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
                response = await set_default_account(
                    account_repo=self.account_repo,
                    user_repo=self.user_repo,
                    user_id=user_id,
                    account_identifier=str(identifier),
                    locale=locale,
                )
            elif action == "unlink":
                response = await unlink_account(
                    account_repo=self.account_repo,
                    direct_debit_provider=self.direct_debit_provider,
                    user_id=user_id,
                    account_identifier=str(identifier),
                    locale=locale,
                )
            elif action in ("check_balance", "balance", "show_balance", "overall_balance"):
                account_identifiers = identifiers or ([str(identifier)] if identifier else None)
                response, viewed_accounts = await check_balance(
                    account_repo=self.account_repo,
                    banking_provider=self.banking_provider,
                    user_id=user_id,
                    account_identifiers=account_identifiers,
                    locale=locale,
                )
            else:
                accounts = user_ctx.get("accounts")
                if accounts:
                    response = AccountFormatter.format_account_list(accounts, locale=locale)
                else:
                    accounts = await self.account_repo.get_by_user(user_id)
                    response = AccountFormatter.format_account_list(accounts, locale=locale)
                viewed_accounts = serialize_accounts(accounts)

            return AccountResult(
                outcome=AccountOutcome.OK,
                response=response,
                patch=patch,
                details={"viewed_accounts": viewed_accounts} if viewed_accounts else {},
            )
        except Exception as e:
            logger.error("account_worker_failed", error=str(e), exc_info=True)
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.error.status_check_failed", locale),
                patch=patch,
            )

    @staticmethod
    def _resolve_locale(user_ctx: dict[str, Any], payload: dict[str, Any]) -> str:
        language = user_ctx.get("language") or payload.get("language")
        return LocaleManager.normalize(language).value
