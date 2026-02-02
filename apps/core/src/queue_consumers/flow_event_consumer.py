"""Flow event consumer for processing PIN verification and other flow events."""

import asyncio
from typing import Any

from apps.core.src.agent.orchestrator.graph.orchestrator import OrchestratorAgent
from shared.queue.messages import FLOW_EVENTS_QUEUE, OUTBOX_QUEUE, FlowEventType
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class FlowEventConsumer:
    """Consumes flow events (like PIN verified) and triggers appropriate service resumption."""

    def __init__(
        self,
        redis_queue: RedisQueue,
        orchestrator: OrchestratorAgent,
    ):
        self.queue = redis_queue
        self.orchestrator = orchestrator
        self.running = False

    async def process_event(self, event_data: dict[str, Any]) -> None:
        """Process a flow event from the queue."""
        try:
            event_type = event_data.get("event_type")
            flow_type = event_data.get("flow_type")
            phone_number = event_data.get("phone_number")
            idempotency_key = event_data.get("idempotency_key")
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

        except Exception as e:
            logger.error(
                "flow_event_processing_failed",
                error=str(e),
                event_data=event_data,
                exc_info=True,
            )

    async def _handle_pin_verified(
        self,
        flow_type: str,
        phone_number: str,
        success: bool,
        channel: str,
    ) -> None:
        """Handle PIN verified event by resuming the appropriate service."""
        if not success:
            logger.warning("pin_verified_but_not_success", phone=phone_number)
            return

        try:
            logger.info("resuming_via_orchestrator", phone=phone_number, flow=flow_type)
            response = await self.orchestrator.resume_transaction(
                phone_number=phone_number, flow_type=flow_type, pin_verified=True
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
                        await self.queue.enqueue(
                            queue_name=OUTBOX_QUEUE,
                            message={
                                "phone_number": phone_number,
                                "channel": channel,
                                "intents": intents_to_send,
                                "metadata": {"source": "flow_event_consumer", "flow_type": flow_type}
                            }
                        )
                        logger.info("pin_response_enqueued_outbox", phone=phone_number, count=len(intents_to_send))

        except Exception as e:
            logger.error(
                "service_resume_failed",
                flow_type=flow_type,
                phone=phone_number,
                error=str(e),
                exc_info=True,
            )

    async def start(self, queue_name: str = FLOW_EVENTS_QUEUE):
        """Start the flow event consumer."""
        self.running = True
        logger.info("flow_event_consumer_starting", queue=queue_name)

        await self.queue.connect()
        while self.running:
            try:
                event_data = await self.queue.dequeue_blocking(queue_name=queue_name, timeout=5)
                if event_data:
                    # Process in background to not block the consumer
                    asyncio.create_task(self.process_event(event_data))

            except asyncio.CancelledError:
                logger.info("flow_event_consumer_cancelled")
                break
            except Exception as e:
                logger.error("flow_event_consumer_error", error=str(e), exc_info=True)
                await asyncio.sleep(1)

        await self.queue.close()
        logger.info("flow_event_consumer_stopped")

    def stop(self):
        """Stop the flow event consumer."""
        self.running = False
