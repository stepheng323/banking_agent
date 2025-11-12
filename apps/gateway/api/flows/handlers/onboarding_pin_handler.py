"""Handler for PIN_ENTRY screen (onboarding flow)."""

import asyncio
from typing import Any, Dict

from fastapi.responses import Response

from shared.clients.whatsapp_client import WhatsAppClient
from shared.models import CreateAccount, UserCreate, UserUpdate
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils import hash_plaintext, is_valid_pin_format

from apps.gateway.api.flows.response_helpers import format_error_response, format_success_response
from apps.gateway.api.flows.verification import get_verification_data


async def handle_onboarding_pin(
    data: Dict[str, Any],
    flow_token: str,
    request_was_encrypted: bool,
    aes_key_bytes: bytes,
    iv_bytes: bytes,
    whatsapp_client: WhatsAppClient,
) -> Response:
    """
    Handle PIN_ENTRY screen for onboarding flow.
    
    Validates PIN, creates/updates user and accounts, returns SUCCESS screen.
    """
    pin = data.get("pin")
    if not pin:
        return format_error_response(
            "PIN_ENTRY",
            "PIN is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    is_valid = is_valid_pin_format(pin)
    print(f"🔍 Is valid: {is_valid}")

    if not is_valid:
        return format_error_response(
            "PIN_ENTRY",
            "Invalid PIN. Please enter a 4 or 6-digit numeric PIN.",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    if not flow_token:
        return format_error_response(
            "PIN_ENTRY",
            "flow_token is required",
            request_was_encrypted,
            aes_key_bytes,
            iv_bytes,
        )

    verification_data = await get_verification_data(flow_token)
    selected_accounts = verification_data.get("selected_accounts", [])
    persisted_accounts = verification_data.get("accounts_full", [])

    hashed_pin = hash_plaintext(pin)
    print(f"🔍 Hashed PIN: {hashed_pin}")

    with UnitOfWork() as uow:
        if not uow.users or not uow.accounts:
            return format_error_response(
                "PIN_ENTRY",
                "Database error",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

        phone_number = flow_token.split("-")[-1]
        print(f"🔍 Phone number: {phone_number}")
        if not phone_number:
            return format_error_response(
                "PIN_ENTRY",
                "Phone number missing",
                request_was_encrypted,
                aes_key_bytes,
                iv_bytes,
            )

        existing_user = uow.users.get_by_phone(phone_number)

        if existing_user:
            user_update = UserUpdate(
                full_name=verification_data.get("full_name"),
                email=verification_data.get("email"),
                transaction_pin=hashed_pin,
                onboarding_status="onboarding_completed",
                extra_data=verification_data,
            )
            user = uow.users.update_user(str(existing_user.id), user_update)
        else:
            user = uow.users.register_user(
                UserCreate(
                    phone_number=phone_number,
                    full_name=verification_data.get("full_name"),
                    email=verification_data.get("email"),
                    transaction_pin=hashed_pin,
                    onboarding_status="onboarding_completed",
                    extra_data=verification_data,
                )
            )
            print(f"🔍 Registering new user: {user}")

        for account_id in selected_accounts:
            account_data = next(
                (acc for acc in persisted_accounts if acc.get("id") == account_id),
                None,
            )

            if account_data:
                existing_account = uow.accounts.get_by_account_id(account_data["id"])

                should_create = True
                if existing_account is not None:
                    if getattr(existing_account, "user_id", None) == str(user.id):
                        should_create = False

                if should_create:
                    new_account = uow.accounts.create_account(
                        CreateAccount(
                            user_id=str(user.id),
                            account_id=account_data["id"],
                            account_number=account_data.get("account_number", ""),
                            bank_name=account_data.get("bank_name", ""),
                            account_name=account_data.get("account_name", ""),
                            extra_data=account_data,
                        )
                    )
                    print(f"Created account: {new_account}")

    response = format_success_response(
        "SUCCESS",
        request_was_encrypted,
        aes_key_bytes,
        iv_bytes,
        extension_message_response={
            "params": {
                "flow_token": flow_token or "completed",
                "bvn": verification_data.get("bvn"),
                "otp": verification_data.get("otp"),
                "pin": pin,
                "accounts_count": len(selected_accounts),
                "success": True,
            }
        },
    )

    asyncio.create_task(
        whatsapp_client.send_text(
            to=phone_number,
            text="🎉 Welcome to Fusepay! Your onboarding is complete. you can now start using the app to send and receive money.",
        )
    )

    return response

