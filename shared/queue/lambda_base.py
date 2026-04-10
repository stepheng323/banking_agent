"""Base Lambda handler utility for SQS event processing."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from shared.queue.contracts import resolve_contract_from_domain
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class BaseSQSHandler:
    """Utility class for building SQS/SNS-to-Lambda bridge handlers."""

    def __init__(self, name: str, dependency_loader: Callable[[], Awaitable[Any] | Any]):
        self.name = name
        self.dependency_loader = dependency_loader
        self._deps: Any | None = None
        self._active_record_context: dict[str, Any] | None = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()

    async def _ensure_deps(self) -> Any:
        if self._deps is None:
            logger.info(f"{self.name}_loading_dependencies")
            loaded = self.dependency_loader()
            if isawaitable(loaded):
                loaded = await loaded
            self._deps = loaded
        return self._deps

    def handle(self, event: dict[str, Any], context: Any) -> dict[str, Any]:
        """Entry point for Lambda."""
        del context
        if self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(self.process_event(event))

    async def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Process all records in the SQS event."""
        records = event.get("Records", [])
        logger.info(f"{self.name}_started", record_count=len(records))

        deps = await self._ensure_deps()

        processed_count = 0
        failed_count = 0
        batch_item_failures: list[dict[str, str]] = []

        for record in records:
            message_id = record.get("messageId")
            record_context = self._extract_record_context(record)
            try:
                payload = self._extract_payload(record)
                self._active_record_context = record_context
                logger.info(
                    f"{self.name}_processing_record",
                    message_id=message_id,
                    queue_name=record_context.get("queue_name"),
                    logical_topic=record_context.get("logical_topic"),
                )

                await self.process_record(payload, deps)
                processed_count += 1

            except Exception as e:
                logger.error(f"{self.name}_record_failed", message_id=message_id, error=str(e), exc_info=True)
                failed_count += 1
                # Report failure for individual record if SQS Batch Item Failures is enabled
                if message_id:
                    batch_item_failures.append({"itemIdentifier": message_id})
            finally:
                self._active_record_context = None

        result = {"status": "completed", "processed": processed_count, "failed": failed_count}

        if batch_item_failures:
            result["batchItemFailures"] = batch_item_failures

        return result

    async def process_record(self, payload: dict[str, Any], deps: Any) -> None:
        """Override this method to implement specific logic."""
        raise NotImplementedError("Subclasses must implement process_record")

    def _extract_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        """Extract JSON payload from SQS record.

        With RawMessageDelivery=true the body is the raw JSON message.
        Legacy SNS→SQS envelopes (TopicArn/Message wrapper) are also handled.
        """
        body = record.get("body", "{}")
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "Message" in data and "TopicArn" in data:
                return json.loads(data["Message"])
            return data
        except json.JSONDecodeError:
            logger.warning(f"{self.name}_json_decode_error", body=body[:100])
            return {}

    def _extract_record_context(self, record: dict[str, Any]) -> dict[str, Any]:
        """Extract contextual metadata from an SQS event record.

        With RawMessageDelivery the SNS MessageAttributes are forwarded as
        SQS MessageAttributes, so we can read the `domain` attribute directly.
        """
        event_source_arn = record.get("eventSourceARN") or record.get("eventSourceArn")
        queue_name = self._extract_queue_name(event_source_arn)

        domain = self._extract_domain_attribute(record)
        logical_topic = None
        if domain:
            contract = resolve_contract_from_domain(domain)
            if contract:
                logical_topic = contract.logical_topic

        return {
            "message_id": record.get("messageId"),
            "queue_name": queue_name,
            "domain": domain,
            "logical_topic": logical_topic,
            "event_source_arn": event_source_arn,
        }

    @staticmethod
    def _extract_domain_attribute(record: dict[str, Any]) -> str | None:
        """Extract the `domain` value from SQS MessageAttributes.

        With SNS RawMessageDelivery, SNS MessageAttributes become
        SQS MessageAttributes in the event record.
        """
        attrs = record.get("messageAttributes") or record.get("MessageAttributes") or {}
        domain_attr = attrs.get("domain", {})
        return domain_attr.get("stringValue") or domain_attr.get("StringValue")

    @staticmethod
    def _extract_queue_name(event_source_arn: str | None) -> str | None:
        if not event_source_arn:
            return None
        return event_source_arn.rsplit(":", maxsplit=1)[-1]

    def get_active_record_context(self) -> dict[str, Any]:
        """Return metadata for currently processed record."""
        return self._active_record_context or {}
