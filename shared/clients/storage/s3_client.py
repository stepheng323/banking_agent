"""S3 client for uploading and managing receipt images."""

import os
from datetime import datetime

import aioboto3
from botocore.exceptions import ClientError

from shared.config.settings import settings
from shared.utils.datetime import utc_now_naive


class S3Client:
    """S3 client for receipt image storage."""

    def __init__(self):
        self.bucket_name = settings.s3_bucket_name
        self.region = settings.s3_region
        self.receipt_prefix = settings.s3_receipt_prefix
        self.session = aioboto3.Session()

    async def upload_receipt_image(
        self, image_bytes: bytes, transaction_id: str, timestamp: datetime | None = None
    ) -> str:
        """
        Upload receipt image to S3.

        Args:
            image_bytes: PNG image bytes
            transaction_id: Transaction ID for path generation
            timestamp: Optional timestamp for filename (defaults to now)

        Returns:
            S3 URL of uploaded image
        """
        if timestamp is None:
            timestamp = utc_now_naive()

        # Generate S3 key: receipts/{transaction_id}/{timestamp}.png
        timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")
        s3_key = f"{self.receipt_prefix}/{transaction_id}/{timestamp_str}.png"

        try:
            async with self.session.client(
                "s3",
                region_name=self.region,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            ) as s3:
                # Upload with public-read ACL
                # Note: If bucket doesn't allow ACLs, configure bucket policy for public access
                await s3.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=image_bytes,
                    ContentType="image/png",
                    ACL="public-read",  # Make receipts publicly accessible
                )

                # Generate public URL
                s3_url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{s3_key}"
                print(f"✓ Receipt uploaded to S3: {s3_url}")
                return s3_url

        except ClientError as e:
            print(f"❌ Error uploading receipt to S3: {e}")
            raise
        except Exception as e:
            print(f"❌ Unexpected error uploading receipt to S3: {e}")
            raise

    async def get_receipt_url(self, transaction_id: str, timestamp: datetime | None = None) -> str | None:
        """
        Get S3 URL for a receipt (if it exists).

        Args:
            transaction_id: Transaction ID
            timestamp: Optional timestamp (defaults to most recent)

        Returns:
            S3 URL if found, None otherwise
        """
        # Note: timestamp parameter reserved for future filtering
        _ = timestamp  # Suppress unused parameter warning

        s3_key_prefix = f"{self.receipt_prefix}/{transaction_id}/"

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
                    s3_url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{key}"
                    return s3_url

                return None

        except ClientError as e:
            print(f"⚠️  Error retrieving receipt URL from S3: {e}")
            return None
        except Exception as e:
            print(f"⚠️  Unexpected error retrieving receipt URL from S3: {e}")
            return None
