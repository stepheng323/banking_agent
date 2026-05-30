"""Handler for BVN_ENTRY screen."""

from fastapi.responses import Response

from apps.gateway.api.webhooks.whatsapp.flows.response_helpers import (
    format_error_response,
    format_success_response,
)
from apps.gateway.api.webhooks.whatsapp.flows.session_owner import (
    format_owner_error_response,
    verify_whatsapp_flow_session_owner,
)
from banking.accounts.onboarding.runtime import bvn_service
from banking.accounts.onboarding.session import ServiceResult
from banking.persistence.unit_of_work import UnitOfWork
from shared.database.enums import UserOnboardingStatusEnum
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


async def handle_bvn_entry(
    bvn: str,
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    authorizing_channel_user_id: str | None = None,
) -> Response:
    """Handle BVN_ENTRY screen - validates BVN and initiates verification."""

    if not bvn:
        return format_error_response(
            "BVN_ENTRY",
            "BVN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not flow_token:
        return format_error_response(
            "BVN_ENTRY",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    owner_check = await verify_whatsapp_flow_session_owner(
        flow_token=flow_token,
        authorizing_channel_user_id=authorizing_channel_user_id,
        screen="BVN_ENTRY",
    )
    if not owner_check.ok:
        return format_owner_error_response("BVN_ENTRY", request_was_encrypted, aes_key_bytes, iv_bytes)

    try:
        session = owner_check.session or await bvn_service.get_session_data(flow_token)
        phone_number = (session or {}).get("phone_number", "")
        if phone_number:
            async with UnitOfWork() as uow:
                if uow.users:
                    user = await uow.users.get_by_phone(phone_number)
                    if (
                        user
                        and getattr(user, "onboarding_status", None)
                        == UserOnboardingStatusEnum.ONBOARDING_COMPLETED.value
                    ):
                        logger.info(
                            "onboarding_already_completed",
                            phone_hash=log_fingerprint(phone_number),
                        )
                        return format_error_response(
                            "BVN_ENTRY",
                            "You have already completed onboarding. "
                            "Please continue using the bot to make transactions.",
                            request_was_encrypted,
                            aes_key_bytes,
                            iv_bytes,
                        )
    except Exception as e:
        logger.warning("onboarding_guard_check_failed", error=str(e))
    # ─────────────────────────────────────────────────────────────────

    result = ServiceResult(**await bvn_service.initiate_bvn_verification(flow_token, bvn))

    if result.success:
        result_data = result.data or {}
        return format_success_response(
            "METHOD_SELECTION",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
            bvn=result_data["bvn"],
            methods=result_data["methods"],
            show_error=False,
            error_message="",
        )

    return format_error_response(
        "BVN_ENTRY",
        result.error or "",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        bvn=bvn,
    )
