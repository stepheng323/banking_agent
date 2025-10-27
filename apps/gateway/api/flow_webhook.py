"""
Flow Webhook for handling WhatsApp Flow data exchange.
This endpoint handles BVN and OTP verification during the flow.
"""

import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Dict, Any, Optional
import json
from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.flow_decryption import decrypt_flow_data, is_encrypted
from shared.utils.flow_encryption import encrypt_flow_response

router = APIRouter()


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
verification_storage = {}


@router.post("/webhook/flow")
async def flow_webhook(req: Request):
    """
    Handle WhatsApp Flow data exchange.
    This is called when user interacts with flow screens or for health checks.
    """
    try:
        body = await req.json()
        print(f"📥 Flow webhook received")

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
                print(f"   ❌ Decryption failed - returning HTTP 421 per Meta spec")
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
            print(f"   ℹ️  Unencrypted request - will return plain JSON")

        print(f"   Screen: {screen}")
        print(f"   Data: {data}")
        print(f"   Flow token: {flow_token}")

        if screen == "BVN_ENTRY":
            bvn = data.get("bvn")
            if not bvn:
                return JSONResponse(
                    content={"error": "BVN is required"}, status_code=400
                )

            print(f"🔍 Verifying BVN: {bvn}")

            is_valid = bvn and len(bvn) == 11 and bvn.isdigit()

            if is_valid:
                verification_storage[flow_token] = {
                    "phone_number": flow_token.split("_")[-1],
                    "bvn": bvn,
                    "bvn_verified": True,
                }

                print(f"✅ BVN verified successfully")
                response = {
                    "screen": "OTP_VERIFICATION",
                    "data": {"screen_0_BVN_0": bvn},
                }

                if request_was_encrypted:
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes
                    )
                    return Response(content=encrypted_response, media_type="text/plain")

                return JSONResponse(content=response)
            else:
                print(f"❌ BVN verification failed")
                response = {
                    "screen": "BVN_ENTRY",
                    "data": {
                        "error_message": "Invalid BVN. Please check and enter a valid 11-digit BVN."
                    },
                }

                if request_was_encrypted:
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes
                    )
                    return Response(content=encrypted_response, media_type="text/plain")

                return JSONResponse(content=response)

        elif screen == "OTP_VERIFICATION":
            otp = data.get("otp")
            bvn = data.get("bvn")

            if not otp:
                return JSONResponse(
                    content={"error": "OTP is required"}, status_code=400
                )

            print(f"🔍 Verifying OTP: {otp}")

            is_valid = otp and len(otp) == 6 and otp.isdigit()

            if is_valid:
                if flow_token in verification_storage:
                    verification_storage[flow_token]["otp_verified"] = True
                    verification_storage[flow_token]["otp"] = otp

                print(f"✅ OTP verified successfully")

                # Fetch user's bank accounts (mock data for now)
                # In production, fetch from your banking API using BVN
                accounts_full = [
                    {
                        "id": "acc_001",
                        "account_number": "0760505261",
                        "account_name": "Access Bank",
                    },
                    {
                        "id": "acc_002",
                        "account_number": "0123456789",
                        "account_name": "GTBank",
                    },
                    {
                        "id": "acc_003",
                        "account_number": "9876543210",
                        "account_name": "Zenith Bank",
                    },
                ]

                accounts_flow = [
                    {
                        "id": acc["id"],
                        "title": f"{acc['account_name']} - {acc['account_number']}",
                    }
                    for acc in accounts_full
                ]

                # Get BVN from storage or current data
                stored_bvn = (
                    verification_storage.get(flow_token, {}).get("bvn")
                    if flow_token
                    else None
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
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes
                    )
                    return Response(content=encrypted_response, media_type="text/plain")

                return JSONResponse(content=response)
            else:
                print(f"❌ OTP verification failed")

                response = {
                    "screen": "OTP_VERIFICATION",
                    "data": {
                        "error_message": "Invalid OTP. Please check and enter the correct 6-digit OTP."
                    },
                }

                if request_was_encrypted:
                    encrypted_response = encrypt_flow_response(
                        response, aes_key_bytes, iv_bytes
                    )
                    return Response(content=encrypted_response, media_type="text/plain")

                return JSONResponse(content=response)

        elif screen == "ACCOUNT_SELECTION":
            accounts = data.get("selected_accounts", [])
            verification_data = verification_storage.get(flow_token, {})
            verification_data.update({"selected_accounts": accounts})

            print(f"🔍 Verification data: {verification_data}")

            # Todo: save the accounts with all relevant data to the database
            # send message to the user about success acount linking

            with UnitOfWork() as uow:
                user = uow.users.register_user(
                    phone_number=verification_data.get("phone_number"),
                    full_name=verification_data.get("full_name"),
                    email=verification_data.get("email"),
                    extra_data=verification_data,
                )

                for account in accounts:
                    uow.accounts.create_account(
                        user_id=user.id,
                        account_number=account.get("account_number"),
                        account_name=account.get("account_name"),
                        bank_name=account.get("bank_name"),
                        extra_data=account,
                    )

            response = {
                "screen": "SUCCESS",
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token or "completed",
                            "bvn": verification_data.get("bvn"),
                            "otp": verification_data.get("otp"),
                            "accounts": accounts,
                            "success": True,
                        }
                    }
                },
            }

            if request_was_encrypted:
                encrypted_response = encrypt_flow_response(
                    response, aes_key_bytes, iv_bytes
                )
                return Response(content=encrypted_response, media_type="text/plain")

            return JSONResponse(content=response)

        print(f" 🏥 Health check (unknown screen: {screen})")
        health_response = {"data": {"status": "active"}}

        if request_was_encrypted:
            encrypted_response = encrypt_flow_response(
                health_response, aes_key_bytes, iv_bytes
            )
            return Response(content=encrypted_response, media_type="text/plain")

        return Response(content=json.dumps(health_response), media_type="text/plain")

    except Exception as e:
        print(f"❌ Error in flow webhook: {e}")
        import traceback

        traceback.print_exc()
        return JSONResponse(content={"error": "Internal server error"}, status_code=500)
