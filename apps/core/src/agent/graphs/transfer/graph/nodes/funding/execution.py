"""Debit execution and status tracking.

Contains functions for initiating and monitoring direct debits
from multiple source accounts.
"""

import asyncio

from apps.core.src.agent.graphs.transfer.state import TransferState
from shared.clients.abstractions import DirectDebitProvider
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def initiate_debits(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
) -> TransferState:
    """
    Initiate direct debits for each funding step.
    """
    funding_steps = state.get("funding_steps", [])
    idempotency_key = state.get("idempotency_key", "")

    if not funding_steps:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No funding steps to execute.",
        }

    initiated_steps = []

    for step in funding_steps:
        reference = f"{idempotency_key}_{step['sequence']}"

        try:
            result = await direct_debit_provider.initiate_debit(
                mandate_id=step["mandate_id"],
                amount=step["amount"],
                reference=reference,
                narration=f"Transfer funding step {step['sequence']}",
            )

            initiated_steps.append(
                {
                    **step,
                    "debit_id": result.debit_id,
                    "reference": reference,
                    "status": result.status.value if result.success else "failed",
                    "error": result.error_message,
                    "provider": direct_debit_provider.provider_name,
                }
            )

            logger.info(
                "debit_initiated",
                step=step["sequence"],
                debit_id=result.debit_id,
                success=result.success,
            )

        except Exception as e:
            logger.error("debit_initiation_failed", step=step["sequence"], error=str(e))
            initiated_steps.append(
                {
                    **step,
                    "status": "failed",
                    "error": str(e),
                }
            )

    failed = [s for s in initiated_steps if s.get("status") == "failed"]

    if failed:
        return {
            **state,
            "funding_steps": initiated_steps,
            "flow_state": "error",
            "funding_status": "failed",
            "funding_error": f"Failed to initiate {len(failed)} debit(s).",
        }

    return {
        **state,
        "funding_steps": initiated_steps,
        "flow_state": "awaiting_debits",
        "funding_status": "debiting",
    }


async def wait_for_debits(
    state: TransferState,
    direct_debit_provider: DirectDebitProvider,
) -> TransferState:
    """
    Check status of all initiated debits.

    Returns:
        - If all successful: flow_state=initiating_payout
        - If any failed: flow_state=error, triggers refund
        - If still pending: flow_state=awaiting_debits (retry later)
    """
    funding_steps = state.get("funding_steps", [])

    if not funding_steps:
        return {
            **state,
            "flow_state": "error",
            "funding_error": "No funding steps to check.",
        }

    updated_steps = []
    all_successful = True
    any_failed = False
    any_pending = False

    for step in funding_steps:
        debit_id = step.get("debit_id")
        current_status = step.get("status", "pending")

        if current_status == "successful":
            updated_steps.append(step)
            continue

        if current_status == "failed":
            updated_steps.append(step)
            any_failed = True
            all_successful = False
            continue

        if not debit_id:
            updated_steps.append({**step, "status": "failed", "error": "No debit_id"})
            any_failed = True
            all_successful = False
            continue

        try:
            result = await direct_debit_provider.get_debit_status(debit_id)
            new_status = result.status.value if result.success else "failed"

            updated_steps.append(
                {
                    **step,
                    "status": new_status,
                    "error": result.error_message if not result.success else None,
                }
            )

            if new_status == "successful":
                logger.info("debit_confirmed", debit_id=debit_id)
            elif new_status == "failed":
                any_failed = True
                all_successful = False
                logger.error("debit_failed", debit_id=debit_id, error=result.error_message)
            else:
                any_pending = True
                all_successful = False
                logger.info("debit_still_pending", debit_id=debit_id, status=new_status)

        except Exception as e:
            logger.error("debit_status_check_failed", debit_id=debit_id, error=str(e))
            updated_steps.append({**step, "status": "pending"})
            any_pending = True
            all_successful = False

    if any_failed:
        return {
            **state,
            "funding_steps": updated_steps,
            "flow_state": "error",
            "funding_status": "failed",
            "funding_error": "One or more debits failed. Initiating refund.",
        }

    if all_successful:
        logger.info(
            "wait_for_debits_all_successful",
            flow_state="initiating_payout",
            funding_status="funded",
        )
        return {
            **state,
            "funding_steps": updated_steps,
            "flow_state": "initiating_payout",
            "funding_status": "funded",
            "response": None,
            "llm_reply": None,
        }

    if any_pending:
        await asyncio.sleep(3)

    return {
        **state,
        "funding_steps": updated_steps,
        "flow_state": "awaiting_debits",
        "funding_status": "debiting",
    }
