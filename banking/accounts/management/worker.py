"""Account management worker (stateless)."""

import asyncio
import time
from decimal import Decimal
from typing import Any

from langchain_core.language_models import BaseChatModel

from banking.accounts.management.balances import check_balance
from banking.accounts.management.capabilities import (
    AccountCapability,
    check_capabilities,
    generate_limitation_message,
)
from banking.accounts.management.formatter import AccountFormatter
from banking.accounts.management.linking import build_link_account_flow
from banking.accounts.management.mutations import missing_identifier_prompt, set_default_account
from banking.accounts.management.serialization import serialize_accounts
from banking.accounts.mandate_state import effective_mandate_status
from banking.accounts.repositories.account_repository import AccountRepository
from banking.identity.repositories.user_repository import UserRepository
from banking.persistence.unit_of_work import UnitOfWork
from banking.presentation.i18n.locale import LocaleManager
from banking.presentation.i18n.renderer import render_message
from banking.runtime.results import AccountOutcome, AccountResult
from shared.cache.flow_session_manager import FlowSessionManager
from shared.cache.user_data import UserDataCache
from shared.clients.abstractions.banking import BankDataProvider
from shared.clients.abstractions.direct_debit import DirectDebitProvider
from shared.messaging.body_blocks import MessageDocument
from shared.types.balance import BalanceQueryContract
from shared.types.conversation_sets import (
    AccountLifecycleContract,
    BulkMutationRequest,
    BulkMutationReviewSnapshot,
    ConversationSetState,
    EntitySelectionRef,
)
from shared.types.read import ReadRequest, ReadResult, normalize_read_request
from shared.utils.bank_aliases import normalize_bank_name
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def _account_value(account: Any, key: str, default: Any = None) -> Any:
    return account.get(key, default) if isinstance(account, dict) else getattr(account, key, default)


def _account_version_token(account: Any) -> str | None:
    updated_at = _account_value(account, "updated_at")
    if updated_at is None:
        return None
    isoformat = getattr(updated_at, "isoformat", None)
    return str(isoformat() if callable(isoformat) else updated_at)


def _filter_lifecycle_accounts(accounts: list[Any], contract: AccountLifecycleContract | None) -> list[Any]:
    if contract is None:
        return accounts
    bank_filter = normalize_bank_name(contract.bank_name or "")
    mandate_statuses = {status.casefold() for status in contract.mandate_statuses}
    readiness_statuses = {status.casefold() for status in contract.readiness_statuses}
    filtered: list[Any] = []
    for account in accounts:
        bank_name = normalize_bank_name(str(_account_value(account, "bank_name", "") or ""))
        status = str(effective_mandate_status(account) or "").casefold()
        is_default = bool(_account_value(account, "is_default", False))
        if bank_filter and bank_filter not in bank_name and bank_name not in bank_filter:
            continue
        if mandate_statuses and status not in mandate_statuses:
            continue
        if readiness_statuses and status not in readiness_statuses:
            continue
        if contract.default_state == "default" and not is_default:
            continue
        if contract.default_state == "not_default" and is_default:
            continue
        filtered.append(account)
    return filtered


def _filter_selected_accounts(accounts: list[Any], selected_ids: set[str]) -> list[Any]:
    if not selected_ids:
        return accounts
    return [
        account
        for account in accounts
        if str(_account_value(account, "account_id", "") or _account_value(account, "id", "")) in selected_ids
    ]

ACTION_CAPABILITY_MAP: dict[str, AccountCapability] = {
    "list_accounts": AccountCapability.LIST_ACCOUNTS,
    "count": AccountCapability.LIST_ACCOUNTS,
    "check_balance": AccountCapability.LIST_ACCOUNTS,
    "get_default": AccountCapability.LIST_ACCOUNTS,
    "set_default": AccountCapability.SET_DEFAULT,
    "unlink": AccountCapability.UNLINK_ACCOUNT,
    "link": AccountCapability.LINK_ACCOUNT,
    "reinitiate_mandate": AccountCapability.LINK_ACCOUNT,
    "close_account": AccountCapability.CLOSE_ACCOUNT,
    "change_bvn": AccountCapability.CHANGE_BVN,
    "add_joint_holder": AccountCapability.ADD_JOINT_HOLDER,
}


def _say_outbox(text: str | None, body_blocks: MessageDocument | None = None) -> list[dict[str, Any]]:
    if not text or not body_blocks:
        return []
    return [{"type": "say", "text": text, "body_blocks": body_blocks}]


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
        del llm
        self.banking_provider = banking_provider
        self.session_manager = session_manager
        self.direct_debit_provider = direct_debit_provider

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> AccountResult:
        """Execute account management logic and return a structured result."""
        del pin_verified, user_message
        patch: dict[str, Any] = {}

        user_ctx = {
            "profile": context.get("profile"),
            "accounts": context.get("accounts", []),
            "language": context.get("language"),
        }
        locale = self._resolve_locale(user_ctx, payload)

        action = payload.get("action")
        identifier = payload.get("identifier")
        identifiers = payload.get("identifiers")
        raw_balance_contract = payload.get("balance_contract")
        raw_lifecycle_contract = payload.get("account_lifecycle_contract")
        try:
            balance_contract = (
                BalanceQueryContract.model_validate(raw_balance_contract)
                if isinstance(raw_balance_contract, dict)
                else None
            )
        except ValueError:
            balance_contract = None
        try:
            lifecycle_contract = (
                AccountLifecycleContract.model_validate(raw_lifecycle_contract)
                if isinstance(raw_lifecycle_contract, dict)
                else None
            )
        except ValueError:
            lifecycle_contract = None
        if lifecycle_contract is not None:
            patch["account_lifecycle_contract"] = lifecycle_contract.model_dump(
                mode="json",
                exclude_none=True,
            )
        read_request = normalize_read_request(payload)
        if read_request is not None and (
            (read_request.subject == "balance" and balance_contract is None)
            or (
                read_request.subject in {"linked_account", "default_account"}
                and lifecycle_contract is None
            )
        ):
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.error.status_check_failed", locale),
            )
        if read_request is None and action in {
            "list_accounts",
            "count",
            "check_balance",
            "get_default",
        }:
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.error.status_check_failed", locale),
            )
        response_shape = read_request.response_shape if read_request is not None else ""
        selected_entity_ids = {
            str(value)
            for value in payload.get("selected_entity_ids", [])
            if isinstance(value, str) and value
        }
        balance_default_scope = False
        if read_request is not None:
            if read_request.subject == "balance":
                action = "check_balance"
                identifier = identifier or read_request.bank_name
            elif read_request.subject == "default_account":
                action = "get_default"
            elif read_request.subject == "linked_account":
                action = "count" if response_shape in {"fact_count", "fact_bool"} else "list"
                identifier = identifier or read_request.bank_name
        if balance_contract is not None:
            action = "check_balance"
            if balance_contract.account_scope == "named":
                identifiers = balance_contract.bank_names
                identifier = balance_contract.bank_names[0] if len(balance_contract.bank_names) == 1 else None
            elif balance_contract.account_scope == "default":
                balance_default_scope = True
            else:
                identifiers = None
                identifier = None

        if action == "list_accounts":
            action = "list"
            patch["action"] = action

        if response_shape in {"fact_count", "fact_bool"} and action == "list":
            action = "count"
            patch["action"] = action

        if action == "unknown" or not action:
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.error.status_check_failed", locale),
                patch=patch,
            )

        capability = ACTION_CAPABILITY_MAP.get(str(action))
        if capability:
            missing_caps = check_capabilities([capability])
            if missing_caps:
                logger.info("capability_blocked", domain="account", capabilities=[cap.value for cap in missing_caps])
                response = generate_limitation_message(missing_caps, locale=locale)
                return AccountResult(outcome=AccountOutcome.OK, response=response, patch=patch)

        if action in ("unlink", "set_default", "reinitiate_mandate") and not identifier:
            if action == "reinitiate_mandate":
                accounts = user_ctx.get("accounts") or []
                pending_accounts = [acc for acc in accounts if getattr(acc, "mandate_status", "") == "pending"]

                if not pending_accounts:
                    return AccountResult(
                        outcome=AccountOutcome.OK,
                        response=render_message("account.error.no_pending_mandate", locale),
                        patch=patch,
                    )

                if len(pending_accounts) == 1 and not payload.get("reinitiate_clarified"):
                    patch["reinitiate_clarified"] = True
                    return AccountResult(
                        outcome=AccountOutcome.NEEDS_INPUT,
                        required_fields=["identifier"],
                        prompt=render_message(
                            "account.prompt.reinitiate_clarify",
                            locale,
                            {"bank_name": pending_accounts[0].bank_name}
                        ),
                        patch=patch,
                    )
                elif len(pending_accounts) == 1:
                    identifier = pending_accounts[0].bank_name
                    patch["identifier"] = identifier
            if not identifier:
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

        raw_set_state = payload.get("conversation_set_state")
        if action in {"set_default", "reinitiate_mandate"} and isinstance(raw_set_state, dict):
            try:
                set_state = ConversationSetState.model_validate(raw_set_state)
            except ValueError:
                set_state = None
            selected_ref = set_state.focused_ref if set_state is not None else None
            if selected_ref is None or selected_ref.entity_id != str(identifier or ""):
                return AccountResult(
                    outcome=AccountOutcome.FAILED,
                    error=render_message("conversation_set.stale_selection", locale),
                    patch=patch,
                )
            current_accounts = await self.account_repo.get_by_user(user_id)
            selected_account = next(
                (
                    account
                    for account in current_accounts
                    if str(
                        _account_value(account, "account_id", "")
                        or _account_value(account, "id", "")
                    )
                    == selected_ref.entity_id
                ),
                None,
            )
            if selected_account is None or _account_version_token(selected_account) != selected_ref.version_token:
                return AccountResult(
                    outcome=AccountOutcome.FAILED,
                    error=render_message("conversation_set.stale_selection", locale),
                    patch=patch,
                )

        if balance_default_scope:
            accounts = user_ctx.get("accounts") or await self.account_repo.get_by_user(user_id)
            default_account = next(
                (
                    account
                    for account in accounts
                    if (
                        account.get("is_default", False)
                        if isinstance(account, dict)
                        else getattr(account, "is_default", False)
                    )
                ),
                None,
            )
            if default_account is None:
                return AccountResult(
                    outcome=AccountOutcome.OK,
                    response=render_message("account.default_unavailable", locale),
                    patch=patch,
                )
            default_bank = (
                default_account.get("bank_name")
                if isinstance(default_account, dict)
                else getattr(default_account, "bank_name", None)
            )
            identifiers = [str(default_bank)] if default_bank else None

        if action == "count":
            accounts = user_ctx.get("accounts") or []
            if not accounts and user_id:
                accounts = await self.account_repo.get_by_user(user_id)
            accounts = _filter_selected_accounts(
                _filter_lifecycle_accounts(list(accounts), lifecycle_contract),
                selected_entity_ids,
            )
            if lifecycle_contract is None and read_request is not None and read_request.bank_name:
                fallback_contract = AccountLifecycleContract(bank_name=read_request.bank_name)
                accounts = _filter_lifecycle_accounts(list(accounts), fallback_contract)
            count = len(accounts)
            if response_shape == "fact_bool":
                response = render_message(
                    "account.list.exists_yes" if count else "account.list.exists_no",
                    locale,
                    {"bank_name": read_request.bank_name if read_request else ""},
                )
                return AccountResult(
                    outcome=AccountOutcome.OK,
                    response=response,
                    patch=patch,
                    read_result=(
                        ReadResult(request=read_request, total_count=count, returned_count=0)
                        if read_request is not None
                        else None
                    ),
                )
            if count == 0:
                lines = [render_message("account.list.count_zero", locale)]
            elif count == 1:
                lines = [render_message("account.list.count_one", locale)]
            else:
                lines = [render_message("account.list.count_many", locale, {"count": count})]
            return AccountResult(
                outcome=AccountOutcome.OK,
                response="\n".join(lines),
                patch=patch,
                read_result=(
                    ReadResult(request=read_request, total_count=count, returned_count=0)
                    if read_request is not None
                    else None
                ),
            )

        if action == "get_default":
            accounts = user_ctx.get("accounts") or []
            if not accounts and user_id:
                accounts = await self.account_repo.get_by_user(user_id)
            default_account = next(
                (
                    account
                    for account in accounts
                    if (
                        account.get("is_default", False)
                        if isinstance(account, dict)
                        else getattr(account, "is_default", False)
                    )
                ),
                None,
            )
            response = AccountFormatter.format_default_account(default_account, locale=locale)
            return AccountResult(
                outcome=AccountOutcome.OK,
                response=response,
                patch=patch,
                details={"viewed_accounts": serialize_accounts([default_account])} if default_account else {},
                read_result=(
                    ReadResult(
                        request=read_request,
                        total_count=1 if default_account else 0,
                        returned_count=0,
                    )
                    if read_request is not None
                    else None
                ),
            )

        viewed_accounts: list[dict[str, Any]] = []
        body_blocks: MessageDocument | None = None
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
            elif action == "reinitiate_mandate":
                from banking.accounts.management.serialization import find_account_by_bank_name
                from banking.accounts.onboarding.mandate import MandateService

                accounts = user_ctx.get("accounts") or []
                if not accounts:
                    accounts = await self.account_repo.get_by_user(user_id)

                selected_account = None
                try:
                    account_index = int(str(identifier))
                    if 1 <= account_index <= len(accounts):
                        selected_account = accounts[account_index - 1]
                except ValueError:
                    selected_account = next(
                        (
                            account
                            for account in accounts
                            if str(
                                _account_value(account, "account_id", "")
                                or _account_value(account, "id", "")
                            )
                            == str(identifier)
                        ),
                        None,
                    ) or find_account_by_bank_name(accounts, str(identifier))

                if not selected_account:
                    response = render_message(
                        "account.account_not_found_with_count",
                        locale,
                        {
                            "identifier": str(identifier),
                            "count": len(accounts),
                        },
                    )
                else:
                    if getattr(selected_account, "mandate_status", "") != "pending":
                        response = render_message("account.error.mandate_not_pending", locale)
                    else:
                        mandate_service = MandateService()
                        result = await mandate_service.reinitiate_mandate(
                            phone_number=profile.get("phone_number") or "",
                            account_id=str(selected_account.account_id),
                            channel="whatsapp"
                        )
                        if result.get("success"):
                            # The outbox message with instructions is enqueued by MandateService
                            response = render_message("account.mandate_reinitiated_success", locale)
                        else:
                            response = result.get("error") or "Failed to reinitiate mandate."
            elif action == "set_default":
                response = await set_default_account(
                    account_repo=self.account_repo,
                    user_repo=self.user_repo,
                    user_id=user_id,
                    account_identifier=str(identifier),
                    locale=locale,
                )
            elif action == "unlink":
                reviewed_payload = payload
                if not isinstance(payload.get("bulk_mutation"), dict):
                    direct_request = await self._build_direct_unlink_request(
                        user_id=user_id,
                        identifier=str(identifier),
                        locale=locale,
                        patch=patch,
                    )
                    if isinstance(direct_request, AccountResult):
                        return direct_request
                    reviewed_payload = {
                        **payload,
                        "bulk_mutation": direct_request.model_dump(mode="json", exclude_none=True),
                    }
                return await self._unlink_reviewed_accounts(
                    user_id=user_id,
                    payload=reviewed_payload,
                    locale=locale,
                    patch=patch,
                )
            elif action == "check_balance":
                bank_name = payload.get("bank_name")
                account_identifiers = identifiers or (
                    [str(identifier)] if identifier else ([str(bank_name)] if bank_name else None)
                )
                response, viewed_accounts = await check_balance(
                    account_repo=self.account_repo,
                    banking_provider=self.banking_provider,
                    user_id=user_id,
                    account_identifiers=account_identifiers,
                    locale=locale,
                )
                total_balance = (
                    sum((Decimal(str(item.get("amount") or "0")) for item in viewed_accounts), Decimal("0.00"))
                    if len(viewed_accounts) > 1
                    else None
                )
                if balance_contract is not None and balance_contract.operation == "compare":
                    viewed_accounts.sort(key=lambda item: Decimal(str(item.get("amount") or "0")), reverse=True)
                    response = AccountFormatter.format_balance_response(
                        viewed_accounts,
                        total_balance,
                        locale=locale,
                    )
                elif (
                    balance_contract is not None
                    and balance_contract.operation == "total"
                    and total_balance is not None
                ):
                    response = render_message(
                        "account.balance.total",
                        locale,
                        {"total_balance": f"{total_balance:,.2f}"},
                    )
                body_blocks = AccountFormatter.format_balance_response_blocks(
                    viewed_accounts,
                    total_balance,
                    locale=locale,
                )
                if balance_contract is not None and balance_contract.operation == "total" and total_balance is not None:
                    body_blocks = [{"type": "text", "text": response}]
            else:
                accounts = user_ctx.get("accounts")
                if accounts:
                    pass
                else:
                    accounts = await self.account_repo.get_by_user(user_id)
                account_list = _filter_selected_accounts(
                    _filter_lifecycle_accounts(list(accounts or []), lifecycle_contract),
                    selected_entity_ids,
                )
                total_count = len(account_list)
                offset = read_request.offset if read_request is not None else 0
                limit = read_request.page_size if read_request is not None else total_count
                page = account_list[offset : offset + limit]
                has_next = offset + limit < total_count
                if response_shape == "fact_status":
                    ready_count = sum(
                        1 for account in account_list if effective_mandate_status(account) == "ready"
                    )
                    response = render_message(
                        "account.readiness.fact",
                        locale,
                        {"ready_count": ready_count, "total_count": total_count},
                    )
                    viewed_accounts = []
                    body_blocks = None
                else:
                    response = AccountFormatter.format_account_list(
                        page,
                        locale=locale,
                        has_next=has_next,
                    )
                    viewed_accounts = serialize_accounts(page)
                    body_blocks = AccountFormatter.format_account_list_blocks(
                        page,
                        locale=locale,
                        has_next=has_next,
                    )

            read_result = None
            if read_request is not None:
                if action == "check_balance":
                    total = len(viewed_accounts)
                    returned = 0 if response_shape.startswith("fact_") else total
                    read_result = ReadResult(request=read_request, total_count=total, returned_count=returned)
                elif action in ("list", "list_accounts"):
                    read_result = ReadResult(
                        request=read_request,
                        total_count=total_count,
                        returned_count=0 if response_shape.startswith("fact_") else len(viewed_accounts),
                        has_next=has_next,
                        has_previous=read_request.offset > 0,
                    )
            return AccountResult(
                outcome=AccountOutcome.OK,
                response=response,
                patch=patch,
                details={"viewed_accounts": viewed_accounts} if viewed_accounts else {},
                outbox=_say_outbox(response, body_blocks),
                read_result=read_result,
            )
        except Exception as e:
            logger.error("account_worker_failed", error=str(e), exc_info=True)
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.error.status_check_failed", locale),
                patch=patch,
            )

    async def _build_direct_unlink_request(
        self,
        *,
        user_id: str,
        identifier: str,
        locale: str,
        patch: dict[str, Any],
    ) -> BulkMutationRequest | AccountResult:
        """Turn a fresh single-account selection into the reviewed mutation contract."""
        accounts = await self.account_repo.get_by_user(user_id)
        if not accounts:
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.no_linked_accounts", locale),
                patch=patch,
            )

        selected: Any | None = None
        try:
            index = int(identifier)
        except ValueError:
            index = 0
        if 1 <= index <= len(accounts):
            selected = accounts[index - 1]
        else:
            identifier_normalized = normalize_bank_name(identifier)
            identifier_id_matches = [
                account
                for account in accounts
                if str(_account_value(account, "account_id", "") or _account_value(account, "id", ""))
                == identifier
            ]
            bank_matches = [
                account
                for account in accounts
                if identifier_normalized
                and normalize_bank_name(str(_account_value(account, "bank_name", "") or ""))
                == identifier_normalized
            ]
            matches = identifier_id_matches or bank_matches
            if len(matches) == 1:
                selected = matches[0]
            elif len(matches) > 1:
                return AccountResult(
                    outcome=AccountOutcome.NEEDS_INPUT,
                    required_fields=["identifier"],
                    prompt=render_message("conversation_set.selection_required", locale),
                    patch=patch,
                )

        if selected is None:
            return AccountResult(
                outcome=AccountOutcome.NEEDS_INPUT,
                required_fields=["identifier"],
                prompt=render_message(
                    "account.account_not_found_with_count",
                    locale,
                    {"identifier": identifier, "count": len(accounts)},
                ),
                patch=patch,
            )

        account_id = str(
            _account_value(selected, "account_id", "") or _account_value(selected, "id", "")
        )
        version_token = _account_version_token(selected)
        if not account_id or not version_token:
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("conversation_set.stale_selection", locale),
                patch=patch,
            )
        bank_name = str(_account_value(selected, "bank_name", "") or "Account")
        account_number = str(_account_value(selected, "account_number", "") or "")
        suffix = account_number[-4:] if account_number else ""
        display_label = f"{bank_name} (···{suffix})" if suffix else bank_name
        return BulkMutationRequest(
            domain="linked_account",
            action="unlink",
            targets=[
                EntitySelectionRef(
                    entity_type="linked_account",
                    entity_id=account_id,
                    frame_id="direct_account_selection",
                    display_label=display_label,
                    version_token=version_token,
                )
            ],
            idempotency_key=f"unlink:{account_id}:{version_token}",
        )

    async def _unlink_reviewed_accounts(
        self,
        *,
        user_id: str,
        payload: dict[str, Any],
        locale: str,
        patch: dict[str, Any],
    ) -> AccountResult:
        try:
            request = BulkMutationRequest.model_validate(payload.get("bulk_mutation"))
        except (TypeError, ValueError):
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("conversation_set.stale_selection", locale),
                patch=patch,
            )
        if request.domain != "linked_account" or request.action != "unlink":
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("conversation_set.stale_selection", locale),
                patch=patch,
            )

        async with UnitOfWork() as preflight_uow:
            accounts = await preflight_uow.accounts.get_by_user_for_update(user_id)
        by_id = {
            str(getattr(account, "account_id", "") or getattr(account, "id", "")): account
            for account in accounts
        }
        raw_previous_outcomes = payload.get("bulk_item_outcomes")
        previous_outcomes = (
            {
                str(key): str(value)
                for key, value in raw_previous_outcomes.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(raw_previous_outcomes, dict)
            else {}
        )
        selected = [by_id.get(ref.entity_id) for ref in request.targets]
        stale = [
            ref
            for ref, account in zip(request.targets, selected, strict=True)
            if (
                account is None
                and previous_outcomes.get(ref.entity_id) != "succeeded"
            )
            or (
                account is not None
                and previous_outcomes.get(ref.entity_id) != "succeeded"
                and _account_version_token(account) != ref.version_token
            )
        ]
        if stale:
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("conversation_set.stale_selection", locale),
                patch=patch,
            )
        selected_pairs = [
            (ref, account)
            for ref, account in zip(request.targets, selected, strict=True)
            if account is not None and previous_outcomes.get(ref.entity_id) != "succeeded"
        ]
        selected_accounts = [account for _, account in selected_pairs]
        if len(selected_accounts) >= len(accounts):
            return AccountResult(
                outcome=AccountOutcome.FAILED,
                error=render_message("account.unlink.keep_one", locale),
                patch=patch,
            )

        default_selected = any(bool(getattr(account, "is_default", False)) for account in selected_accounts)
        replacement_id = str(
            payload.get("replacement_default_account_id") or payload.get("identifier") or ""
        ).strip()
        selected_ids = {ref.entity_id for ref in request.targets}
        if default_selected and (not replacement_id or replacement_id in selected_ids or replacement_id not in by_id):
            return AccountResult(
                outcome=AccountOutcome.NEEDS_INPUT,
                required_fields=["identifier"],
                prompt=render_message("account.unlink.choose_remaining_default", locale),
                patch={**patch, "bulk_mutation": request.model_dump(mode="json", exclude_none=True)},
            )

        confirmation = payload.get("confirmation")
        confirmed = isinstance(confirmation, dict) and confirmation.get("confirmed") is True
        if not confirmed:
            snapshot = BulkMutationReviewSnapshot(
                request=request,
                created_at_ts=time.time(),
            )
            summary = render_message(
                "account.unlink.review",
                locale,
                {"count": len(request.targets), "items": "\n".join(ref.display_label for ref in request.targets)},
            )
            return AccountResult(
                outcome=AccountOutcome.NEEDS_CONFIRMATION,
                confirmation_summary=summary,
                confirmation_snapshot=snapshot.model_dump(mode="json", exclude_none=True),
                patch={**patch, "bulk_mutation": request.model_dump(mode="json", exclude_none=True)},
            )

        outcomes: list[str] = []
        failed_count = 0
        replacement_applied = False
        item_outcomes = dict(previous_outcomes)
        for ref in request.targets:
            if previous_outcomes.get(ref.entity_id) == "succeeded":
                outcomes.append(
                    render_message("account.unlink.item_success", locale, {"item": ref.display_label})
                )
        for ref, account in selected_pairs:
            mandate_id = getattr(account, "mandate_id", None)
            if mandate_id:
                try:
                    provider_result = await self.direct_debit_provider.cancel_mandate(mandate_id)
                    if provider_result is False:
                        raise RuntimeError("mandate_revocation_failed")
                except Exception:
                    failed_count += 1
                    outcomes.append(
                        render_message("account.unlink.item_failed", locale, {"item": ref.display_label})
                    )
                    item_outcomes[ref.entity_id] = "failed"
                    continue
            try:
                async with UnitOfWork() as uow:
                    if default_selected and not replacement_applied:
                        await uow.accounts.set_default_account(user_id, replacement_id)
                        replacement_applied = True
                    deleted = await uow.accounts.delete_account(
                        ref.entity_id,
                        user_id,
                        assign_new_default=False,
                    )
                    if not deleted:
                        raise RuntimeError("local_unlink_failed")
                    await uow.commit()
                outcomes.append(
                    render_message("account.unlink.item_success", locale, {"item": ref.display_label})
                )
                item_outcomes[ref.entity_id] = "succeeded"
            except Exception:
                failed_count += 1
                outcomes.append(render_message("account.unlink.item_failed", locale, {"item": ref.display_label}))
                item_outcomes[ref.entity_id] = "failed"

        logger.info(
            "conversation_set_mutation_completed",
            domain="linked_account",
            selected_count=len(request.targets),
            stale_count=0,
            partial_failure_count=failed_count,
        )
        if len(request.targets) > failed_count:
            try:
                user = await self.user_repo.get_by_id(user_id)
                if user is not None:
                    asyncio.create_task(UserDataCache().invalidate_accounts(user.phone_number))
            except Exception:
                logger.warning("account_unlink_cache_invalidation_deferred")
        response = render_message(
            "account.unlink.bulk_result",
            locale,
            {"items": "\n".join(outcomes)},
        )
        remaining_accounts = await self.account_repo.get_by_user(user_id)
        refreshed_request = ReadRequest(subject="linked_account", response_shape="surface_list")
        refreshed_contract = AccountLifecycleContract(operation="list", response_shape="surface_list")
        return AccountResult(
            outcome=AccountOutcome.OK,
            response=response,
            details={"viewed_accounts": serialize_accounts(remaining_accounts[:5])},
            read_result=ReadResult(
                request=refreshed_request,
                total_count=len(remaining_accounts),
                returned_count=min(5, len(remaining_accounts)),
                has_next=len(remaining_accounts) > 5,
            ),
            patch={
                **patch,
                "bulk_mutation": (
                    request.model_dump(mode="json", exclude_none=True)
                    if failed_count
                    else None
                ),
                "bulk_item_outcomes": item_outcomes,
                "invalidate_conversation_set_domain": "linked_account",
                "account_lifecycle_contract": refreshed_contract.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            },
        )

    @staticmethod
    def _resolve_locale(user_ctx: dict[str, Any], payload: dict[str, Any]) -> str:
        language = user_ctx.get("language") or payload.get("language")
        return LocaleManager.normalize(language).value

    @staticmethod
    def _account_preview_lines(accounts: list[Any]) -> list[str]:
        lines: list[str] = []
        for account in accounts:
            if isinstance(account, dict):
                bank_name = str(account.get("bank_name") or account.get("bank") or "Account").strip()
                account_number = str(account.get("account_number") or account.get("number") or "").strip()
            else:
                bank_name = str(getattr(account, "bank_name", None) or getattr(account, "bank", None) or "Account")
                account_number = str(getattr(account, "account_number", None) or getattr(account, "number", None) or "")
            suffix = f" • …{account_number[-4:]}" if account_number else ""
            if bank_name:
                lines.append(f"• {bank_name}{suffix}")
        return lines
