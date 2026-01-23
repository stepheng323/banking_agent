"""Account management worker (stateless)."""

from typing import TYPE_CHECKING, Any

from apps.core.src.agent.graphs.account.capabilities import (
    check_capabilities,
    derive_requirements,
    generate_limitation_message,
)
from apps.core.src.agent.graphs.account.formatter import AccountFormatter
from apps.core.src.agent.orchestrator.models.domain import (
    AccountOutcome,
    AccountResult,
)
from shared.utils.logging import get_logger

if TYPE_CHECKING:
    from apps.core.src.agent.graphs.account.service import AccountService

logger = get_logger(__name__)


class AccountWorker:
    """Stateless worker for account tasks."""

    def __init__(self, service: "AccountService") -> None:
        self.service = service
        self.parser = service.parser

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
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
                flow_data = await self.service.build_link_account_flow(context)
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
                response = await self.service.set_default(user_id, str(identifier))
            elif action == "unlink":
                response = await self.service.unlink_account(user_id, str(identifier))
            else:
                accounts = user_ctx.get("accounts")
                if accounts:
                    response = AccountFormatter.format_account_list(accounts)
                else:
                    response = await self.service.list_accounts(user_id)

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
            return await self.service._translate_response(text, language)
        return text
