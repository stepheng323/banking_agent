"""Transaction creation service for flow webhook."""

from typing import Any, Dict

from shared.repositories.unit_of_work import UnitOfWork


async def create_transfer_transaction(
    pending_transfer: Dict[str, Any],
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
        print(f"✅ Transaction record created: {transaction_id}")
        return transaction_id

