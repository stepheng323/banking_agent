"""Receipt formatter for transaction receipts - generates image receipts."""

from typing import Optional
from datetime import datetime

from shared.database.models import Transaction
from shared.services.receipt_generator import ReceiptGenerator
from shared.clients.s3_client import S3Client
from shared.repositories.account_repository import AccountRepository


async def generate_receipt_image(
    transaction: Transaction,
    account_repo: AccountRepository,
    receipt_generator: ReceiptGenerator,
    s3_client: S3Client,
) -> str:
    """
    Generate receipt image from transaction and upload to S3.

    Args:
        transaction: Transaction database model instance
        account_repo: Account repository to fetch account name
        receipt_generator: Receipt generator service
        s3_client: S3 client for uploading images

    Returns:
        S3 URL of the uploaded receipt image
    """
    # Get account name from Account model
    account_name: Optional[str] = None
    if transaction.source_account_id:
        account = account_repo.get_by_id(str(transaction.source_account_id))
        if account:
            account_name = account.account_name

    # Generate receipt image
    image_bytes = await receipt_generator.generate_receipt_image(
        transaction, account_name
    )

    # Upload to S3
    transaction_id = str(transaction.id)
    s3_url = await s3_client.upload_receipt_image(
        image_bytes, transaction_id, transaction.created_at
    )

    return s3_url
