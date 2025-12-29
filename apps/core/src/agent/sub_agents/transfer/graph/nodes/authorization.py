"""Authorization node for transfer flow."""

from typing import cast

from apps.core.src.agent.tools.response import (
    ResponseIntent,
    build_response_context,
    get_synthesizer,
)
from apps.core.src.agent.sub_agents.transfer.graph.nodes.utils import debug_log
from apps.core.src.agent.sub_agents.transfer.state import TransferState
from shared.cache.redis_client import Redis
from shared.database.connection import get_db
from shared.database.models import (
    FundedTransfer,
    FundedTransferStatusEnum,
    FundingStep,
    FundingStepStatusEnum,
)
from shared.queue.redis_queue import RedisQueue
from shared.services.auth import AuthorizationService
from shared.services.transactions import create_transfer_transaction
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def authorize_transaction(
    state: TransferState,
    redis_client: Redis,
    queue: RedisQueue,
    authorization_service: AuthorizationService | None = None,
) -> TransferState:
    """Authorize transaction after PIN verification."""
    phone_number = state.get("phone_number")
    idem_key = state.get("idempotency_key")
    synthesizer = get_synthesizer()

    logger.info(
        "authorize_transaction_ENTRY",
        phone=phone_number,
        idem_key=idem_key,
        flow_state=state.get("flow_state"),
        funding_status=state.get("funding_status"),
    )
    debug_log(
        f"🔐 authorize_transaction ENTRY: phone={phone_number}, idem_key={idem_key}, flow_state={state.get('flow_state')}, transfer_status={state.get('transfer_status')}"
    )

    if not idem_key:
        context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "response": response,
                "flow_state": "error",
                "transfer_status": "failed",
            },
        )

    if not authorization_service:
        authorization_service = AuthorizationService(redis_client=redis_client)

    pin_result = await authorization_service.get_pin_verification_result(idem_key)
    pin_verified_in_state = state.get("pin_verified")

    if not pin_result:
        if pin_verified_in_state is True:
            pass
        else:
            return cast(
                TransferState,
                {
                    **state,
                    "response": "",  # No response needed, WhatsApp Flow handles PIN
                    "flow_state": "authorizing",
                },
            )
    elif not pin_result.verified:
        error_msg = pin_result.error or "PIN verification failed"
        retry_count = pin_result.retry_count

        if retry_count >= 3:
            await redis_client.delete(f"user:{phone_number}:pending_transfer")
            context = build_response_context(ResponseIntent.MAX_ATTEMPTS_EXCEEDED, state, error_message=error_msg)
            response = await synthesizer.synthesize(context)
            return cast(
                TransferState,
                {
                    **state,
                    "response": response,
                    "flow_state": "error",
                    "transfer_status": "failed",
                    "pin_verified": False,
                    "pin_verification_error": error_msg,
                    "pin_retry_count": retry_count,
                },
            )

        context = build_response_context(ResponseIntent.PIN_FAILED, state, error_message=error_msg)
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "response": response,
                "flow_state": "confirming",
                "pin_verified": False,
                "pin_verification_error": error_msg,
                "pin_retry_count": retry_count,
            },
        )

    try:
        amount = state.get("amount")
        recipient_account = state.get("recipient_account")
        recipient_bank_name = state.get("recipient_bank_name")
        recipient_bank_code = state.get("recipient_bank_code")
        recipient_name = state.get("recipient_name")

        if not amount or not recipient_account:
            logger.warning(
                "authorization_missing_data",
                amount=amount,
                recipient_account=recipient_account,
            )
            context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
            response = await synthesizer.synthesize(context)
            return {
                **state,
                "response": response,
                "transfer_status": "failed",
                "flow_state": "completed",
            }

        pending_transfer = {
            "amount": amount,
            "recipient": {
                "account_number": recipient_account,
                "bank_name": recipient_bank_name,
                "bank_code": recipient_bank_code,
                "name": recipient_name,
            },
            "idempotency_key": state.get("idempotency_key"),
        }

        user_id = None
        if pin_result and pin_result.user_id:
            user_id = pin_result.user_id
        else:
            user_profile = state.get("user_profile")
            if isinstance(user_profile, dict):
                user_id = user_profile.get("id")
            if not user_id:
                logger.warning(
                    "authorization_missing_user_id",
                    pin_result=bool(pin_result),
                    user_profile=bool(user_profile),
                )
                context = build_response_context(ResponseIntent.SESSION_EXPIRED, state)
                response = await synthesizer.synthesize(context)
                return cast(
                    TransferState,
                    {
                        **state,
                        "response": response,
                        "flow_state": "error",
                        "transfer_status": "failed",
                    },
                )

        pending_transfer["user_id"] = user_id
        funding_required = state.get("funding_required", False)
        funding_steps = state.get("funding_steps", [])
        funded_transfer_id = None

        if funding_required and funding_steps:
            db = next(get_db())
            try:
                funded_transfer = FundedTransfer(
                    user_id=user_id,
                    amount=amount,
                    recipient_account_number=recipient_account,
                    recipient_bank_code=recipient_bank_code,
                    recipient_bank_name=recipient_bank_name,
                    recipient_name=recipient_name,
                    idempotency_key=idem_key,
                    status=FundedTransferStatusEnum.PAYOUT_PENDING.value,  # Debits already completed
                )
                db.add(funded_transfer)
                db.flush()  # Get the ID
                funded_transfer_id = str(funded_transfer.id)

                # Create FundingStep records
                from datetime import datetime

                for idx, step in enumerate(funding_steps, start=1):
                    # Map status from funding step to FundingStepStatusEnum
                    step_status = step.get("status", "pending")
                    if step_status == "successful":
                        funding_step_status = FundingStepStatusEnum.CONFIRMED.value
                    elif step_status == "failed":
                        funding_step_status = FundingStepStatusEnum.FAILED.value
                    elif step_status == "pending":
                        funding_step_status = FundingStepStatusEnum.PENDING.value
                    else:
                        funding_step_status = step_status  # Use as-is if already in enum format

                    # Detect provider from environment or default to mono for production
                    # In development/test, we use "mock"
                    provider_name = step.get("provider", "mono")  # Default to mono unless specified

                    funding_step = FundingStep(
                        funded_transfer_id=funded_transfer.id,
                        account_id=step.get("account_id"),
                        amount=step.get("amount"),
                        sequence=idx,
                        provider_name=provider_name,
                        provider_debit_id=step.get("debit_id"),
                        status=funding_step_status,
                        initiated_at=datetime.utcnow(),  # Timestamp when debit was initiated
                        confirmed_at=datetime.utcnow() if step_status == "successful" else None,
                        error_message=step.get("error"),
                    )
                    db.add(funding_step)

                db.commit()
                logger.info(
                    "funded_transfer_created",
                    funded_transfer_id=funded_transfer_id,
                    num_steps=len(funding_steps),
                    total_amount=amount,
                )
            except Exception as e:
                db.rollback()
                logger.error("funded_transfer_creation_failed", error=str(e), exc_info=True)
                # Continue anyway - transaction will still be created
            finally:
                db.close()

        transaction_id = await create_transfer_transaction(
            pending_transfer,
            user_id,
            idem_key,
        )

        # Link transaction to funded transfer if this was a multi-account funding
        if funded_transfer_id:
            from shared.repositories.unit_of_work import UnitOfWork

            with UnitOfWork() as uow:
                try:
                    transaction = uow.transactions.get(transaction_id)
                    if transaction:
                        transaction.funded_transfer_id = funded_transfer_id
                        uow.commit()
                        logger.info(
                            "transaction_linked_to_funded_transfer",
                            transaction_id=transaction_id,
                            funded_transfer_id=funded_transfer_id,
                        )
                except Exception as e:
                    uow.rollback()
                    logger.error("failed_to_link_transaction_to_funded_transfer", error=str(e), exc_info=True)

        transfer_request = {
            "type": "execute_transfer",
            "phone_number": phone_number,
            "idempotency_key": idem_key,
            "transfer_data": pending_transfer,
            "transaction_id": transaction_id,
        }

        logger.info("authorization_enqueueing_transfer", transaction_id=transaction_id, idem_key=idem_key)
        await queue.enqueue_simple(
            queue_name="banking:transactions",
            message=transfer_request,
        )
        logger.info("authorization_enqueued_transfer", transaction_id=transaction_id)

        prev_values_key = f"transfer:prev:session:{phone_number}"
        await redis_client.delete(prev_values_key)

        pin_verification_key = f"transaction:pin_verified:{idem_key}"
        await redis_client.delete(pin_verification_key)

        retry_count = pin_result.retry_count if pin_result else 0
        # Return empty response - the executor will send the final success/failure notification
        # This prevents race condition between auth message and executor notification
        result = cast(
            TransferState,
            {
                **state,
                "response": "",  # Empty - executor sends final notification
                "flow_state": "completed",
                "transfer_status": "authorized",
                "pin_verified": True,
                "pin_retry_count": retry_count,
            },
        )
        return result

    except Exception as e:
        logger.error(
            "authorization_error",
            error=str(e),
            phone=phone_number,
            exc_info=True,
        )
        context = build_response_context(
            ResponseIntent.TRANSFER_FAILED,
            state,
            error_message="Failed to process authorization. Please try again.",
        )
        response = await synthesizer.synthesize(context)
        return cast(
            TransferState,
            {
                **state,
                "response": response,
                "flow_state": "error",
                "transfer_status": "failed",
            },
        )
