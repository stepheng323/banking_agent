"""Account linking flow construction."""

import hashlib
import secrets
from typing import Any

from shared.i18n.locale import LocaleManager
from shared.i18n.renderer import render_message
from shared.utils.logging import get_logger

logger = get_logger(__name__)


def new_link_flow_token() -> str:
    return f"link-{secrets.token_urlsafe(32)}"


def token_fingerprint(flow_token: str | None) -> str:
    if not flow_token:
        return ""
    return hashlib.sha256(str(flow_token).encode("utf-8")).hexdigest()[:16]


async def build_link_account_flow(
    *,
    context: dict[str, Any],
    banking_provider: Any,
    session_manager: Any,
) -> dict[str, Any]:
    """Build account linking flow."""
    from banking.accounts.onboarding.session import OnboardingStep
    from shared.config.settings import settings

    flow_id = settings.whatsapp.account_linking_flow_id
    locale = LocaleManager.normalize(context.get("language")).value
    if not flow_id:
        return {"error": render_message("account.linking.unavailable", locale)}

    phone_number = context.get("phone_number", "")
    profile = context.get("profile") or {}
    canonical_phone_number = str(profile.get("phone_number") or phone_number or "").strip()
    bvn = (profile.get("extra_data") or {}).get("bvn")

    if not bvn:
        return {"error": render_message("account.linking.bvn_missing", locale)}

    result = await banking_provider.initiate_bvn_lookup(bvn)
    if not result.success:
        return {"error": result.error_message or render_message("account.linking.start_failed", locale)}

    methods = [{"id": m["method"], "title": m["hint"]} for m in result.verification_methods]
    flow_token = new_link_flow_token()
    channel = context.get("channel", "whatsapp")
    session_payload = {
        "phone_number": canonical_phone_number,
        "bvn": bvn,
        "session_id": result.session_id,
        "methods": methods,
        "step": OnboardingStep.METHOD_SELECTION.value,
        "is_account_linking": True,
        "channel": channel,
    }
    if channel == "telegram":
        session_payload["channel_user_id"] = str(context.get("channel_user_id") or phone_number or "").strip()
    if not session_manager:
        logger.error("account_linking_session_manager_missing", flow_token_hash=token_fingerprint(flow_token))
        return {"error": render_message("account.linking.start_failed", locale)}

    stored = await session_manager.update_session_strict(
        flow_token,
        session_payload,
        verify=True,
    )
    if not stored:
        logger.error(
            "account_linking_session_create_failed",
            flow_token_hash=token_fingerprint(flow_token),
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
