"""Transaction creation service for transfers and airtime."""

from typing import Any

from sqlalchemy.exc import IntegrityError

from shared.repositories.unit_of_work import UnitOfWork
from shared.utils.logging import get_logger

logger = get_logger(__name__)


async def create_transfer_transaction(
    pending_transfer: dict[str, Any],
    user_id: str,
    idempotency_key: str,
) -> str:
    """
    Create a transaction record for a transfer.

    Args:
        pending_transfer: Transfer data from Redis
        user_id: User's ID
        idempotency_key: Transfer idempotency key

    Returns:
        Transaction ID
    """
    amount = float(pending_transfer.get("amount", 0))
    recipient = pending_transfer.get("recipient", {})
    source = pending_transfer.get("source", {})
    narration = pending_transfer.get("narration")

    with UnitOfWork() as uow:
        if not uow.transactions:
            raise ValueError("Transaction repository not available")

        try:
            transaction = uow.transactions.create(
                user_id=user_id,
                transaction_type="transfer",
                status="pending",
                amount=amount,
                currency="NGN",
                source_account_id=source.get("id"),
                source_account_number=source.get("account_number", ""),
                source_bank_name=source.get("bank_name", ""),
                recipient_account_number=recipient.get("account_number", ""),
                recipient_bank_code=recipient.get("bank_code", ""),
                recipient_bank_name=recipient.get("bank_name", ""),
                recipient_name=recipient.get("name", ""),
                narration=narration,
                idempotency_key=idempotency_key,
            )
            transaction_id = str(transaction.id)
            uow.commit()
            logger.info("transfer_transaction_created", transaction_id=transaction_id)
            return transaction_id
        except IntegrityError:
            try:
                uow.rollback()
            except Exception:
                pass
            if uow.transactions:
                existing = uow.transactions.get_by_idempotency_key(idempotency_key)
                if existing:
                    existing_id = str(existing.id)
                    logger.info(
                        "transfer_transaction_exists",
                        transaction_id=existing_id,
                        idempotency_key=idempotency_key,
                    )
                    return existing_id
            raise


async def create_airtime_transaction(
    pending_airtime: dict[str, Any],
    user_id: str,
    idempotency_key: str,
) -> str:
    """
    Create a transaction record for an airtime purchase.

    Args:
        pending_airtime: Airtime data from Redis
        user_id: User's ID
        idempotency_key: Airtime purchase idempotency key

    Returns:
        Transaction ID
    """
    amount = float(pending_airtime.get("amount", 0))
    recipient = pending_airtime.get("recipient", {})
    source = pending_airtime.get("source", {})
    narration = pending_airtime.get("narration")

    recipient_phone = recipient.get("phone", "")
    network = recipient.get("network", "")

    with UnitOfWork() as uow:
        if not uow.transactions:
            raise ValueError("Transaction repository not available")

        try:
            transaction = uow.transactions.create(
                user_id=user_id,
                transaction_type="airtime",
                status="pending",
                amount=amount,
                currency="NGN",
                source_account_id=source.get("id"),
                source_account_number=source.get("account_number", ""),
                source_bank_name=source.get("bank_name", ""),
                recipient_account_number=recipient_phone,
                recipient_bank_code=network,
                recipient_bank_name=network,
                recipient_name=recipient.get("name") or recipient_phone,
                narration=narration,
                idempotency_key=idempotency_key,
            )
            transaction_id = str(transaction.id)
            uow.commit()
            logger.info("airtime_transaction_created", transaction_id=transaction_id)
            return transaction_id
        except IntegrityError:
            try:
                uow.rollback()
            except Exception:
                pass
            if uow.transactions:
                existing = uow.transactions.get_by_idempotency_key(idempotency_key)
                if existing:
                    existing_id = str(existing.id)
                    logger.info(
                        "airtime_transaction_exists",
                        transaction_id=existing_id,
                        idempotency_key=idempotency_key,
                    )
                    return existing_id
            raise
