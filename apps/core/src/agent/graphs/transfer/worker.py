"""Transfer Worker (V3).

Stateless domain worker for Transfer tasks.
Executes a single pass through the transfer logic pipeline:
Extract -> Resolve -> Validate -> Confirmation -> Authorization -> Execution.

Returns a standardized TransferResult.
"""

from typing import Any

from apps.core.src.agent.graphs.transfer.models.types import (
    TransferContext,
    TransferGates,
    TransferPayload,
)
from apps.core.src.agent.graphs.transfer.nodes.confirmation import build_confirmation
from apps.core.src.agent.graphs.transfer.nodes.extraction import extract_transfer_update
from apps.core.src.agent.graphs.transfer.nodes.funding import plan_transaction_funding
from apps.core.src.agent.graphs.transfer.nodes.resolver import resolve_beneficiary
from apps.core.src.agent.graphs.transfer.nodes.security import require_auth
from apps.core.src.agent.graphs.transfer.nodes.selection import select_source_account
from apps.core.src.agent.graphs.transfer.nodes.validation import (
    validate_amount,
    validate_transfer,
)
from apps.core.src.agent.orchestrator.models.domain import (
    TransferOutcome,
    TransferResult,
)
from shared.database.models import TransactionStatusEnum
from shared.repositories.transaction_repository import (
    TransactionRepository,
)
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransferWorker:
    """Stateless worker for transfer tasks."""

    def __init__(
        self,
        validation_service,
        beneficiary_repo,
        account_repo,
        queue,
        extractor,
        banking_provider,
        bank_cache,
        transaction_repo: TransactionRepository,
    ):
        self.validation_service = validation_service
        self.beneficiary_repo = beneficiary_repo
        self.account_repo = account_repo
        self.queue = queue
        self.extractor = extractor
        self.banking_provider = banking_provider
        self.bank_cache = bank_cache
        self.transaction_repo = transaction_repo

    async def run(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
        user_message: str | None = None,
        pin_verified: bool = False,
    ) -> TransferResult:
        data = TransferPayload(**payload)
        gates = TransferGates(
            pin_verified=pin_verified,
            confirmation_confirmed=(
                pin_verified or (data.confirmation.confirmed if hasattr(data, "confirmation") else False)
            ),
        )

        idempotency_key = data.idempotency_key
        # Defensive: treat "no-key" as missing to force regeneration
        if not idempotency_key or idempotency_key == "no-key":
            import uuid

            idempotency_key = f"transfer-{uuid.uuid4()}"
            data = data.model_copy(update={"idempotency_key": idempotency_key})
        else:
            pass

        def with_key(result: TransferResult) -> TransferResult:
            """Ensure result patch contains ID key and accumulated state."""
            if result.patch is None:
                result.patch = {}

            if hasattr(data, "model_dump"):
                current_state = data.model_dump(exclude_unset=True)
            else:
                current_state = data.dict(exclude_unset=True)

            result.patch.update(current_state)
            result.patch["idempotency_key"] = idempotency_key
            return result

        try:
            pass
        except Exception:
            pass

        ctx = TransferContext(
            phone_number=context.get("phone_number", ""),
            beneficiaries=context.get("beneficiaries", []),
            accounts=context.get("accounts", []),
        )

        previous_data = data.model_dump()
        updates_detected = []

        extraction_ack = None
        if user_message:
            res = await extract_transfer_update(data, self.extractor, user_message, context)
            if res.patch:
                extraction_ack = res.patch.get("_extraction_ack")
                data = data.model_copy(update=res.patch)

        current_dump = data.model_dump()
        for field in ["amount", "narration", "recipient_bank_name"]:
            val_old = previous_data.get(field)
            val_new = current_dump.get(field)
            if val_new != val_old and val_new is not None:
                updates_detected.append(field.replace("_", " ").capitalize())

        res = await resolve_beneficiary(data, ctx, self.banking_provider, self.bank_cache)
        # print(f"DEBUG: Resolution Outcome: {res.outcome}", flush=True)
        if res.outcome != TransferOutcome.OK:
            return with_key(res)
        if res.patch:
            data = data.model_copy(update=res.patch)

        res = await select_source_account(data, ctx)
        # print(f"DEBUG: Source Selection Outcome: {res.outcome}", flush=True)
        if res.outcome != TransferOutcome.OK:
            return with_key(res)
        if res.patch:
            data = data.model_copy(update=res.patch)

        res = validate_amount(data, ctx)
        # print(f"DEBUG: Validate Amount Outcome: {res.outcome}", flush=True)
        if res.outcome != TransferOutcome.OK:
            return with_key(res)
        if res.patch:
            data = data.model_copy(update=res.patch)

        res = validate_transfer(data)
        # print(f"DEBUG: Validate Transfer Outcome: {res.outcome}", flush=True)
        if res.outcome != TransferOutcome.OK:
            return with_key(res)

        # We need dd_provider injected
        if hasattr(self, "dd_provider"):
            res = await plan_transaction_funding(data, ctx, self.dd_provider)
            # print(f"DEBUG: Funding Plan Outcome: {res.outcome}", flush=True)
            if res.outcome != TransferOutcome.OK:
                return with_key(res)
            if res.patch:
                data = data.model_copy(update=res.patch)

        update_msg = extraction_ack

        res = build_confirmation(data, ctx)
        res.update_message = update_msg

        try:
            if not self.queue._redis:
                await self.queue.connect()

            phone = ctx.phone_number
            # Write both variants to be safe, or just the one Gateway checks first
            await self.queue._redis.setex(
                f"transfer:token:{idempotency_key}:phone",
                3600,
                phone,
            )
            # Also write generic transaction token if needed by unified handler
            await self.queue._redis.setex(
                f"transaction:token:{idempotency_key}:phone",
                3600,
                phone,
            )
        except Exception as e:
            logger.error("failed_to_persist_token", error=str(e))

        if not gates.confirmation_confirmed:
            return with_key(res)

        # Auth Logic
        res = require_auth(gates)
        if res.outcome != TransferOutcome.OK:
            if res.outcome == TransferOutcome.NEEDS_AUTH:
                try:
                    if not self.queue._redis:
                        await self.queue.connect()

                    phone = ctx.phone_number
                    # Force overwrite to ensure freshness
                    await self.queue._redis.setex(
                        f"transfer:token:{idempotency_key}:phone",
                        3600,
                        phone,
                    )
                except Exception as e:
                    logger.error("failed_to_set_auth_token", error=str(e))

            # Ensure patch contains the key (via helper)
            return with_key(res)

        # Execution Logic
        try:
            # Create Transaction Record
            transaction_id = None
            if self.transaction_repo:
                try:
                    # Check existing
                    existing_tx = self.transaction_repo.get_by_idempotency_key(idempotency_key)
                    if existing_tx:
                        transaction_id = str(existing_tx.id)
                    else:
                        tx = self.transaction_repo.create(
                            idempotency_key=idempotency_key,
                            transaction_type="transfer",
                            status=TransactionStatusEnum.PENDING.value,
                            user_id=context.get("user_id"),
                            amount=data.amount,
                            recipient_account_number=data.recipient_account,
                            recipient_bank_code=data.recipient_bank_code,
                            # Use resolved fields if available, else input
                            recipient_name=data.recipient_resolved_name or data.recipient_name or "",
                            recipient_bank_name=data.recipient_bank_name or "",
                            source_account_id=data.source_account_id,
                            # Resolve source details via cache or assume available if validated
                            source_account_number=data.source_account_number or "",
                            source_bank_name=data.source_bank_name or "",
                            narration=data.narration,
                        )
                        transaction_id = str(tx.id)
                        logger.info("transaction_persisted", id=transaction_id, key=idempotency_key)
                except Exception as e:
                    logger.error("failed_to_persist_transaction", error=str(e))
                    # Proceed without ID if persistence fails (or fail hard? user wants persistence)
                    # For now proceed with warning

            if data.funding_plan and not data.funding_plan.get("is_single_source", True):
                await self.queue.enqueue(
                    queue_name="payouts",
                    message={
                        "type": "payout",
                        "idempotency_key": idempotency_key,
                        "transaction_id": transaction_id,
                        "funding_plan": data.funding_plan,
                        "recipient_account": data.recipient_account,
                        "recipient_bank_code": data.recipient_bank_code,
                        "narration": data.narration,
                    },
                )
            else:
                await self.queue.enqueue(
                    queue_name="transfers",
                    message={
                        "type": "execute_transfer",  # Changed from 'transfer' to match Consumer 'execute_transfer'
                        "idempotency_key": idempotency_key,
                        "transaction_id": transaction_id,
                        "phone_number": context.get("phone_number"),
                        "transfer_data": {
                            "amount": data.amount,
                            "recipient": {
                                "account_number": data.recipient_account,
                                "bank_code": data.recipient_bank_code,
                            },
                            "source": {
                                "account_id": data.source_account_id,
                                "account_number": data.source_account_number,
                            },
                            "narration": data.narration,
                        },
                        # Include old fields for backward compat if needed, but 'transfer_data' is what Executor expects
                    },
                )

            receipt_data = {
                "status": "queued",
                "id": idempotency_key,
                "amount": data.amount,
                "recipient_account": data.recipient_account,
                "recipient_bank_code": data.recipient_bank_code,
                "recipient_name": data.recipient_name or data.recipient_resolved_name,
                "narration": data.narration,
                "date": "Now",
            }
            if data.source_bank_name:
                receipt_data["source_bank"] = data.source_bank_name

            return with_key(TransferResult(outcome=TransferOutcome.OK, receipt=receipt_data))
        except Exception as e:
            return with_key(
                TransferResult(outcome=TransferOutcome.FAILED, error=f"Execution failed: {str(e)}", retryable=True)
            )
