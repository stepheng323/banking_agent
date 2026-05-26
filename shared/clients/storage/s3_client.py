"""S3 client for uploading and managing receipt images."""

import inspect
import os
import secrets

import aioboto3
from botocore.exceptions import ClientError

from shared.config.settings import settings
from shared.utils.logging import get_logger, log_fingerprint

logger = get_logger(__name__)


class S3Client:
    """S3 client for receipt image storage."""

    def __init__(self):
        self.bucket_name = settings.s3_bucket_name
        self.region = settings.s3_region
        self.receipt_prefix = settings.s3_receipt_prefix
        self.session = aioboto3.Session()

    async def upload_receipt_image(self, image_bytes: bytes, transaction_id: str) -> str:
        """
        Upload receipt image privately to S3 and return a short-lived GET URL.

        Args:
            image_bytes: PNG image bytes
            transaction_id: Transaction ID used only for a non-reversible key prefix

        Returns:
            Short-lived presigned GET URL for the uploaded image
        """
        transaction_hash = log_fingerprint(transaction_id, length=24)
        nonce = secrets.token_urlsafe(16)
        s3_key = f"{self.receipt_prefix}/{transaction_hash}/{nonce}.png"

        try:
            async with self.session.client(
                "s3",
                region_name=self.region,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            ) as s3:
                await s3.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=image_bytes,
                    ContentType="image/png",
                )

                presigned_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": s3_key},
                    ExpiresIn=900,
                )
                if inspect.isawaitable(presigned_url):
                    presigned_url = await presigned_url

                logger.info(
                    "receipt_uploaded_to_s3",
                    bucket_hash=log_fingerprint(self.bucket_name),
                    key_hash=log_fingerprint(s3_key),
                    transaction_hash=transaction_hash,
                )
                return str(presigned_url)

        except ClientError as e:
            logger.error(
                "receipt_s3_upload_failed",
                error_code=e.response.get("Error", {}).get("Code"),
                transaction_hash=log_fingerprint(transaction_id),
            )
            raise
        except Exception as e:
            logger.error(
                "receipt_s3_upload_failed",
                error_type=type(e).__name__,
                transaction_hash=log_fingerprint(transaction_id),
            )
            raise

    async def get_receipt_url(self, transaction_id: str) -> str | None:
        """
        Get S3 URL for a receipt (if it exists).

        Args:
            transaction_id: Transaction ID

        Returns:
            Short-lived presigned GET URL if found, None otherwise
        """
        s3_key_prefix = f"{self.receipt_prefix}/{log_fingerprint(transaction_id, length=24)}/"

        try:
            async with self.session.client(
                "s3",
                region_name=self.region,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            ) as s3:
                # List objects with prefix
                response = await s3.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=s3_key_prefix,
                    MaxKeys=1,
                )

                if "Contents" in response and len(response["Contents"]) > 0:
                    key = response["Contents"][0]["Key"]
                    presigned_url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": self.bucket_name, "Key": key},
                        ExpiresIn=900,
                    )
                    if inspect.isawaitable(presigned_url):
                        presigned_url = await presigned_url
                    return str(presigned_url)

                return None

        except ClientError as e:
            logger.warning("receipt_s3_lookup_failed", error_code=e.response.get("Error", {}).get("Code"))
            return None
        except Exception as e:
            logger.warning("receipt_s3_lookup_failed", error_type=type(e).__name__)
            return None
