"""Context loading node for airtime purchase flow."""

from typing import Any, cast

from apps.core.src.agent.tools.context import load_user_context_shared
from apps.core.src.agent.sub_agents.airtime.state import AirtimeState


async def load_user_context(
    state: AirtimeState,
    user_cache: Any,
    account_repo: Any,
    beneficiary_repo: Any,
) -> AirtimeState:
    """Load user context (profile, accounts, beneficiaries) for airtime flow."""
    return cast(
        AirtimeState,
        await load_user_context_shared(
            state, user_cache, account_repo, beneficiary_repo, beneficiary_type="airtime"
        )
    )
