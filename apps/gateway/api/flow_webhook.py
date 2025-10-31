"""
Flow Webhook for handling WhatsApp Flow data exchange.
This endpoint handles BVN and OTP verification during the flow.
"""

import traceback
import json
import uuid
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from shared.clients.whatsapp_client import WhatsAppClient
from shared.models import CreateAccount, UserCreate, UserUpdate
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils import (
    decrypt_flow_data,
    encrypt_flow_response,
    hash_plaintext,
    is_encrypted,
    is_valid_pin_format,
)

router = APIRouter()

_whatsapp_client_instance = None


def get_whatsapp_client() -> WhatsAppClient:
    """
    Dependency factory for WhatsApp client.
    Uses lazy initialization and singleton pattern for efficiency.
    """
    global _whatsapp_client_instance
    if _whatsapp_client_instance is None:
        _whatsapp_client_instance = WhatsAppClient()
    return _whatsapp_client_instance


class FlowDataExchangeRequest(BaseModel):
    """Request model for flow data exchange."""

    version: Optional[str] = None
    flow_token: Optional[str] = None
    screen: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class FlowAction(BaseModel):
    """Response model for flow actions."""

    action: str  # 'navigate' or 'complete'
    next_screen: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# In-memory storage for verification state (use Redis in production)
verification_storage: Dict[str, Dict[str, Any]] = {}


@router.post("/webhook/flow")
async def flow_webhook(
    req: Request, whatsapp_client: WhatsAppClient = Depends(get_whatsapp_client)
):
    """
    Handle WhatsApp Flow data exchange.
    This is called when user interacts with flow screens or for health checks.
    """
    try:
        body = await req.json()
        print("📥 Flow webhook received")

        request_was_encrypted = is_encrypted(body)
        aes_key_bytes = None
        iv_bytes = None

        if request_was_encrypted:
            encrypted_data = body["encrypted_flow_data"]
            encrypted_key = body["encrypted_aes_key"]
            iv = body["initial_vector"]

            result = decrypt_flow_data(encrypted_data, encrypted_key, iv)

            print(f"🔍 Result: {result}")

            if not result:
                print("   ❌ Decryption failed - returning HTTP 421 per Meta spec")
                error_response = {
                    "errors": [
                        {
                            "message": "Failed to decrypt request. Please check your encryption configuration."
                        }
                    ]
                }
                return Response(
                    content=json.dumps(error_response),
                    media_type="text/plain",
                    status_code=421,
                )

            decrypted, aes_key_bytes, iv_bytes = result
            screen = decrypted.get("screen")
            data = decrypted.get("data", {})
            flow_token = decrypted.get("flow_token")
            print(f"   ✅ Decrypted flow data: {decrypted}")

        else:
            screen = body.get("screen")
            data = body.get("data", {})
            flow_token = body.get("flow_token")
            print("   ℹ️  Unencrypted request - will return plain JSON")

        print(f"   Screen: {screen}")
        print(f"   Data: {data}")
        print(f"   Flow token: {flow_token}")

        if screen == "BVN_ENTRY":
            bvn = data.get("bvn")
            if not bvn:
                return JSONResponse(content={"error": "BVN is required"}, status_code=400)

            if not flow_token:
                return JSONResponse(content={"error": "flow_token is required"}, status_code=400)

            print(f"🔍 Verifying BVN: {bvn}")

            is_valid = bvn and len(bvn) == 11 and bvn.isdigit()

            if is_valid:
                verification_storage[flow_token] = {
                    "phone_number": flow_token.split("_")[-1],
                    "bvn": bvn,
                    "bvn_verified": True,
                }

                print("✅ BVN verified successfully")
                response = {
                    "screen": "OTP_VERIFICATION",
                    "data": {
                        "bvn": str(bvn),
                        "show_error": False,
                        "error_message": "",
                    },
                }

                if request_was_encrypted:
                    if aes_key_bytes is None or iv_bytes is None:
                        return JSONResponse(
                            content={"error": "Encryption keys missing"}, status_code=500
                        )
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes)
                    return Response(content=encrypted_response, media_type="text/plain")

                return JSONResponse(content=response)

            print("❌ BVN verification failed")
            response = {
                "screen": "BVN_ENTRY",
                "data": {
                    "bvn": str(bvn) if bvn else "",
                    "show_error": True,
                    "error_message": "Invalid BVN. Please check and enter a valid 11-digit BVN.",
                },
            }

            if request_was_encrypted:
                if aes_key_bytes is None or iv_bytes is None:
                    return JSONResponse(
                        content={"error": "Encryption keys missing"}, status_code=500
                    )
                encrypted_response = encrypt_flow_response(
                    response, aes_key_bytes, iv_bytes)
                return Response(content=encrypted_response, media_type="text/plain")

            return JSONResponse(content=response)

        elif screen == "OTP_VERIFICATION":
            otp = data.get("otp")
            bvn = data.get("bvn")

            if not otp:
                return JSONResponse(content={"error": "OTP is required"}, status_code=400)

            print(f"🔍 Verifying OTP: {otp}")

            is_valid = otp and len(otp) == 6 and otp.isdigit()

            if is_valid:
                if flow_token in verification_storage:
                    verification_storage[flow_token]["otp_verified"] = True
                    verification_storage[flow_token]["otp"] = otp

                print("✅ OTP verified successfully")

                # Fetch user's bank accounts (mock data for now)
                # In production, fetch from your banking API using BVN
                accounts_full = [
                    {
                        "id": str(uuid.uuid4()),
                        "account_number": "0760505261",
                        "bank_name": "Access Bank",
                        "account_name": "John Doe",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "account_number": "0123456789",
                        "bank_name": "GTBank",
                        "account_name": "John Doe",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "account_number": "9876543210",
                        "bank_name": "Zenith Bank",
                        "account_name": "John Doe",
                    },
                ]

                # Persist accounts for later steps so IDs remain consistent
                if flow_token:
                    verification_storage.setdefault(flow_token, {})[
                        "accounts_full"
                    ] = accounts_full

                accounts_flow = [
                    {
                        "id": acc["id"],
                        "title": f"{acc['bank_name']} - {acc['account_number']}",
                    }
                    for acc in accounts_full
                ]

                # Get BVN from storage or current data
                stored_bvn = (
                    verification_storage.get(flow_token, {}).get(
                        "bvn") if flow_token else None
                )
                current_bvn = stored_bvn or bvn or ""

                response = {
                    "screen": "ACCOUNT_SELECTION",
                    "data": {
                        "accounts": accounts_flow,
                        "bvn": str(current_bvn),
                        "show_error": False,
                        "error_message": "",
                    },
                }

                if request_was_encrypted:
                    if aes_key_bytes is None or iv_bytes is None:
                        return JSONResponse(
                            content={"error": "Encryption keys missing"}, status_code=500
                        )
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes)
                    return Response(content=encrypted_response, media_type="text/plain")

                return JSONResponse(content=response)

            print("❌ OTP verification failed")

            stored_bvn = (
                verification_storage.get(flow_token, {}).get(
                    "bvn") if flow_token else None
            )
            current_bvn = stored_bvn or bvn or ""

            response = {
                "screen": "OTP_VERIFICATION",
                "data": {
                    "bvn": str(current_bvn),
                    "show_error": True,
                    "error_message": "Invalid OTP. Please check and enter the correct 6-digit OTP.",
                },
            }

            if request_was_encrypted:
                if aes_key_bytes is None or iv_bytes is None:
                    return JSONResponse(
                        content={"error": "Encryption keys missing"}, status_code=500
                    )
                encrypted_response = encrypt_flow_response(
                    response, aes_key_bytes, iv_bytes)
                return Response(content=encrypted_response, media_type="text/plain")

            return JSONResponse(content=response)

        elif screen == "ACCOUNT_SELECTION":
            if not flow_token:
                return JSONResponse(content={"error": "flow_token is required"}, status_code=400)
            accounts = data.get("selected_accounts", [])
            verification_data = verification_storage.get(flow_token, {})
            verification_data.update({"selected_accounts": accounts})

            print(f"🔍 Verification data: {verification_data}")

            response = {
                "screen": "PIN_ENTRY",
                "data": {
                    "show_error": False,
                    "error_message": "",
                },
            }

            if request_was_encrypted:
                if aes_key_bytes is None or iv_bytes is None:
                    return JSONResponse(
                        content={"error": "Encryption keys missing"}, status_code=500
                    )
                encrypted_response = encrypt_flow_response(
                    response, aes_key_bytes, iv_bytes)
                return Response(content=encrypted_response, media_type="text/plain")

            return JSONResponse(content=response)

        elif screen == "PIN_ENTRY":
            pin = data.get("pin")
            if not pin:
                return JSONResponse(content={"error": "PIN is required"}, status_code=400)

            is_valid = is_valid_pin_format(pin)
            print(f"🔍 Is valid: {is_valid}")

            if is_valid:
                if not flow_token:
                    return JSONResponse(
                        content={"error": "flow_token is required"}, status_code=400
                    )
                verification_data = verification_storage.get(flow_token, {})
                selected_accounts = verification_data.get(
                    "selected_accounts", [])
                persisted_accounts = verification_data.get("accounts_full", [])

                hashed_pin = hash_plaintext(pin)
                print(f"🔍 Hashed PIN: {hashed_pin}")
                with UnitOfWork() as uow:
                    if not uow.users or not uow.accounts:
                        return JSONResponse(
                            content={"error": "Database error"}, status_code=500
                        )
                    phone_number = flow_token.split("-")[-1]
                    print(f"🔍 Phone number: {phone_number}")
                    if not phone_number:
                        return JSONResponse(
                            content={"error": "Phone number missing"}, status_code=400
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
                        user = uow.users.update_user(
                            str(existing_user.id), user_update)
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
                            (acc for acc in persisted_accounts if acc.get(
                                "id") == account_id),
                            None,
                        )

                        if account_data:
                            existing_account = uow.accounts.get_by_account_id(
                                account_data["id"])

                            should_create = True
                            if existing_account is not None:
                                if getattr(existing_account, "user_id", None) == str(user.id):
                                    should_create = False

                            if should_create:
                                new_account = uow.accounts.create_account(
                                    CreateAccount(
                                        user_id=str(user.id),
                                        account_id=account_data["id"],
                                        account_number=account_data.get(
                                            "account_number", ""),
                                        bank_name=account_data.get(
                                            "bank_name", ""),
                                        account_name=account_data.get(
                                            "account_name", ""),
                                        extra_data=account_data,
                                    ))
                                print(f"Created account: {new_account}")

                response = {
                    "screen": "SUCCESS",
                    "data": {
                        "extension_message_response": {
                            "params": {
                                "flow_token": flow_token or "completed",
                                "bvn": verification_data.get("bvn"),
                                "otp": verification_data.get("otp"),
                                "pin": pin,
                                "accounts_count": len(selected_accounts),
                                "success": True,
                            }
                        }
                    },
                }

                if request_was_encrypted:
                    if aes_key_bytes is None or iv_bytes is None:
                        return JSONResponse(
                            content={"error": "Encryption keys missing"}, status_code=500
                        )
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes)
                    return Response(content=encrypted_response, media_type="text/plain")

                asyncio.create_task(
                    whatsapp_client.send_text(
                        to=phone_number,
                        text="🎉 Welcome to Fusepay! Your onboarding is complete. you can now start using the app to send and receive money.",
                    )
                )
                return JSONResponse(content=response)

            print("❌ PIN verification failed")
            response = {
                "screen": "PIN_ENTRY",
                "data": {
                    "show_error": True,
                    "error_message": "Invalid PIN. Please enter a 4 or 6-digit numeric PIN.",
                },
            }

            if request_was_encrypted:
                if aes_key_bytes is None or iv_bytes is None:
                    return JSONResponse(
                        content={"error": "Encryption keys missing"}, status_code=500
                    )
                encrypted_response = encrypt_flow_response(
                    response, aes_key_bytes, iv_bytes)
                return Response(content=encrypted_response, media_type="text/plain")

            return JSONResponse(content=response)

        print(f" 🏥 Health check (unknown screen: {screen})")
        health_response = {"data": {"status": "active"}}

        if request_was_encrypted:
            if aes_key_bytes is None or iv_bytes is None:
                return JSONResponse(
                    content={"error": "Encryption keys missing"}, status_code=500
                )
            encrypted_response = encrypt_flow_response(
                health_response, aes_key_bytes, iv_bytes)
            return Response(content=encrypted_response, media_type="text/plain")

        return Response(content=json.dumps(health_response), media_type="text/plain")

    except Exception as e:
        print(f"❌ Error in flow webhook: {e}")

        traceback.print_exc()
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)
