"""Context loading node for transfer flow."""

from typing import Any, cast

from apps.core.src.agent.common.nodes.context import load_user_context_shared
from apps.core.src.agent.transfer.state import TransferState


async def load_user_context(
    state: TransferState,
    user_cache: Any,
    account_repo: Any,
    beneficiary_repo: Any,
) -> TransferState:
    """Load user context (profile, accounts, beneficiaries) for transfer flow."""
    return cast(
        TransferState,
        await load_user_context_shared(
            state, user_cache, account_repo, beneficiary_repo, beneficiary_type="transfer"
        )
    )
