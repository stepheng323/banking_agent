"""Flow event consumer for processing PIN verification and other flow events."""

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from shared.queue.adapter import QueueConsumer, QueuePublisher
from shared.queue.messages import FLOW_EVENTS_QUEUE, FlowEventType
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FlowEventConsumer:
    """Consumes flow events (like PIN verified) and triggers appropriate service resumption."""

    def __init__(
        self,
        publisher: QueuePublisher,
        orchestrator: OrchestratorAgent,
        queue_consumer: QueueConsumer | None = None,
        db_session: AsyncSession | None = None,
    ):
        self.queue_consumer = queue_consumer
        self.publisher = publisher
        self.orchestrator = orchestrator
        self.db_session = db_session
        self.running = False

    async def process_event(self, event_data: dict[str, Any]) -> None:
        """Process a flow event from the queue."""
        try:
            event_type = str(event_data.get("event_type", ""))
            flow_type = str(event_data.get("flow_type", ""))
            phone_number = str(event_data.get("phone_number", ""))
            idempotency_key = str(event_data.get("idempotency_key", ""))
            success = event_data.get("success", False)

            logger.info(
                "flow_event_received",
                event_type=event_type,
                flow_type=flow_type,
                phone=phone_number,
                idem_key=idempotency_key,
            )

            if event_type == FlowEventType.PIN_VERIFIED.value:
                await self._handle_pin_verified(
                    flow_type=flow_type,
                    phone_number=phone_number,
                    success=success,
                    channel=event_data.get("channel", "whatsapp"),
                    extra_data=event_data.get("extra_data"),
                )
            elif event_type == FlowEventType.PIN_FAILED.value:
                logger.info(
                    "pin_verification_failed",
                    phone=phone_number,
                    flow_type=flow_type,
                )
            else:
                logger.warning(
                    "unknown_flow_event",
                    event_type=event_type,
                    event_data=event_data,
                )

            if self.db_session is not None:
                await self.db_session.commit()

        except Exception as e:
            if self.db_session is not None:
                await self.db_session.rollback()
            logger.error(
                "flow_event_processing_failed",
                error=str(e),
                event_data=event_data,
                exc_info=True,
            )
            raise

    async def _handle_pin_verified(
        self,
        flow_type: str,
        phone_number: str,
        success: bool,
        channel: str,
        extra_data: dict[str, Any] | None = None,
    ) -> None:
        """Handle PIN verified event by resuming the appropriate service."""
        if not success:
            logger.warning("pin_verified_but_not_success", phone=phone_number)
            return

        try:
            logger.info("resuming_via_orchestrator", phone=phone_number, flow=flow_type, channel=channel)
            response = await self.orchestrator.resume_transaction(
                phone_number=phone_number, flow_type=flow_type, pin_verified=True, channel=channel
            )

            if response:
                if isinstance(response, dict):
                    text = response.get("text") or response.get("final_response")
                    outbox = response.get("outbox", [])

                    intents_to_send = []
                    if text:
                        intents_to_send.append({"type": "say", "text": text})

                    if outbox:
                        intents_to_send.extend(outbox)

                    if intents_to_send:
                        # Use chat_id from extra_data if available (e.g., Telegram where chat_id != phone_number)
                        outbox_phone = phone_number
                        if extra_data and "chat_id" in extra_data:
                            outbox_phone = extra_data["chat_id"]

                        await self.publisher.publish(
                            topic="notification.send",
                            message={
                                "phone_number": outbox_phone,
                                "channel": channel,
                                "intents": intents_to_send,
                                "metadata": {"source": "flow_event_consumer", "flow_type": flow_type},
                            },
                        )
                        logger.info(
                            "pin_response_enqueued_outbox",
                            outbox_phone=outbox_phone,
                            mapped_from=phone_number,
                            count=len(intents_to_send),
                        )

        except Exception as e:
            logger.error(
                "service_resume_failed",
                flow_type=flow_type,
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )
            raise  # Re-raise to prevent acking on failure

    async def start(self, queue_name: str = FLOW_EVENTS_QUEUE) -> None:
        """Start the flow event consumer."""
        if self.queue_consumer is None:
            raise RuntimeError("flow_event_consumer_requires_queue_consumer")
        self.running = True
        logger.info("flow_event_consumer_starting", queue=queue_name)
        while self.running:
            try:
                result = await self.queue_consumer.consume_one(queue_name=queue_name, timeout=5)
                if result:
                    event_data, receipt_handle = result
                    await self.process_event(event_data)
                    if self.queue_consumer is not None and hasattr(self.queue_consumer, "ack_message"):
                        await self.queue_consumer.ack_message(queue_name, receipt_handle)

            except asyncio.CancelledError:
                logger.info("flow_event_consumer_cancelled")
                break
            except Exception as e:
                logger.error("flow_event_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)
        logger.info("flow_event_consumer_stopped")

    def stop(self) -> None:
        """Stop the flow event consumer."""
        self.running = False
