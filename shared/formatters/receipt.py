"""Receipt formatter for transaction receipts - generates image receipts."""

from shared.clients.storage.s3_client import S3Client
from shared.database.models import Account, Transaction
from shared.services.receipt_generator import ReceiptGenerator


async def generate_receipt_image(
    transaction: Transaction,
    account: Account,
    receipt_generator: ReceiptGenerator,
    s3_client: S3Client,
) -> str:
    """
    Generate receipt image from transaction and upload to S3.

    Args:
        transaction: Transaction database model instance
        account: Account database model instance
        receipt_generator: Receipt generator service
        s3_client: S3 client for uploading images

    Returns:
        S3 URL of the uploaded receipt image
    """
    account_name = account.account_name if account else None

    image_bytes = await receipt_generator.generate_receipt_image(transaction, account_name)

    transaction_id = str(transaction.id)
    s3_url = await s3_client.upload_receipt_image(
        image_bytes, transaction_id, transaction.created_at
    )

    return s3_url
