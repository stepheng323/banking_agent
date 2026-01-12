"""Context loading node for transfer flow."""

from typing import Any, cast

from apps.core.src.agent.graphs.__shared__.account_selection.mandate_validator import (
    validate_mandate_status,
)
from apps.core.src.agent.graphs.__shared__.context import load_user_context_shared
from apps.core.src.agent.graphs.__shared__.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.cache.redis_client import RedisClient
from shared.clients.whatsapp.client import WhatsAppClient


async def load_user_context(
    state: TransferState,
    user_cache: Any,
    account_repo: Any,
    beneficiary_repo: Any,
    user_repo: Any = None,
) -> TransferState:
    """Load user context (profile, accounts, beneficiaries) for transfer flow."""
    result = cast(
        TransferState,
        await load_user_context_shared(
            state,
            user_cache,
            account_repo,
            beneficiary_repo,
            beneficiary_type="transfer",
            user_repo=user_repo,
        ),
    )

    accounts = result.get("accounts", [])
    if accounts:
        has_ready_account = any(validate_mandate_status(acc)[0] for acc in accounts)
        if not has_ready_account:
            default_account = next(
                (acc for acc in accounts if acc.get("is_default")), accounts[0]
            )
            _, error_message, metadata = validate_mandate_status(default_account)

            phone_number = state.get("phone_number")
            synthesizer = get_synthesizer()

            if metadata and metadata.get("needs_reinitiation") and phone_number:
                account_id = default_account.get("account_id")
                if account_id:
                    redis = RedisClient.get_client()
                    pending_key = f"mandate:pending_reinitiation:{phone_number}"
                    await redis.set(pending_key, account_id, ex=300)  # 5 min expiry

                    whatsapp = WhatsAppClient()
                    context = build_response_context(
                        ResponseIntent.MANDATE_REQUIRED,
                        cast(dict[str, Any], result),
                        error_message=error_message,
                    )
                    response = await synthesizer.synthesize(context)

                    await whatsapp.send_button(
                        to=phone_number,
                        body_text=response,
                        buttons=[{"id": "reinitiate_mandate", "title": "Reinitiate"}],
                        message_id=state.get("message_id"),
                    )

                    return cast(
                        TransferState,
                        {
                            **result,
                            "flow_state": "error",
                            "response": "",  # Empty - button already sent
                        },
                    )

            context = build_response_context(
                ResponseIntent.MANDATE_REQUIRED,
                cast(dict[str, Any], result),
                error_message=error_message,
            )
            response = await synthesizer.synthesize(context)
            return cast(
                TransferState,
                {
                    **result,
                    "flow_state": "error",
                    "response": response,
                },
            )

    return result
