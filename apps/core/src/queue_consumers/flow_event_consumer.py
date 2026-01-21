"""Flow event consumer for processing PIN verification and other flow events."""

import asyncio
from typing import Any

from apps.core.src.agent.graphs.airtime.service import AirtimeService
from apps.core.src.agent.graphs.data.service import DataService
from apps.core.src.agent.graphs.transfer.service import TransferService
from apps.core.src.agent.orchestrator.orchestrator import OrchestratorAgent
from apps.core.src.agent.shared.batch.service import BatchService
from shared.clients.whatsapp.client import WhatsAppClient
from shared.queue.messages import FLOW_EVENTS_QUEUE, FlowEventType
from shared.queue.redis_queue import RedisQueue
from shared.utils.logging import get_logger

logger = get_logger(__name__)




class FlowEventConsumer:
    """Consumes flow events (like PIN verified) and triggers appropriate service resumption."""

    def __init__(
        self,
        redis_queue: RedisQueue,
        transfer_service: TransferService | None = None,
        airtime_service: AirtimeService | None = None,
        data_service: DataService | None = None,
        batch_service: BatchService | None = None,
        whatsapp_client: WhatsAppClient | None = None,
        orchestrator: OrchestratorAgent | None = None,
    ):
        self.queue = redis_queue
        self.transfer_service = transfer_service
        self.airtime_service = airtime_service
        self.data_service = data_service
        self.batch_service = batch_service
        self.whatsapp_client = whatsapp_client
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
    ) -> None:
        """Handle PIN verified event by resuming the appropriate service."""
        if not success:
            logger.warning("pin_verified_but_not_success", phone=phone_number)
            return

        try:
            response = None

            # Prefer Orchestrator if available (New Workflow Engine)
            if self.orchestrator:
                logger.info("resuming_via_orchestrator", phone=phone_number, flow=flow_type)
                response = await self.orchestrator.resume_transaction(
                    phone_number=phone_number, flow_type=flow_type, pin_verified=True
                )

            # Legacy fallback / specific services handling if orchestrator didn't handle it
            if not response:
                if flow_type == "transfer":
                    if self.transfer_service:
                        response = await self.transfer_service.resume_after_pin_verification(phone_number, True, None)
                        logger.info("transfer_resumed_after_pin", phone=phone_number)
                    else:
                        logger.error("transfer_service_not_available")

                elif flow_type == "airtime":
                    if self.airtime_service:
                        response = await self.airtime_service.resume_after_pin_verification(phone_number, True, None)
                        logger.info("airtime_resumed_after_pin", phone=phone_number)
                    else:
                        logger.error("airtime_service_not_available")

                elif flow_type == "data":
                    if self.data_service:
                        response = await self.data_service.resume_after_pin_verification(phone_number, True, None)
                        logger.info("data_resumed_after_pin", phone=phone_number)
                    else:
                        logger.error("data_service_not_available")

                elif flow_type == "batch":
                    if self.batch_service and hasattr(self.batch_service, "resume_after_pin_verification"):
                        response = await self.batch_service.resume_after_pin_verification(phone_number, True, None)
                        logger.info("batch_resumed_after_pin", phone=phone_number)
                    else:
                        logger.error("batch_service_not_available")

                else:
                    logger.warning("unknown_flow_type", flow_type=flow_type)

            # Send response to user if available
            if response and self.whatsapp_client:
                if isinstance(response, dict):
                    text = response.get("text") or response.get("final_response")
                    outbox = response.get("outbox", [])

                    if text:
                        await self.whatsapp_client.send_text(phone_number, text)
                        logger.info("pin_response_sent_text", phone=phone_number)

                    for msg in outbox:
                        if msg.get("type") == "say":
                            await self.whatsapp_client.send_text(phone_number, msg.get("text"))
                            logger.info("pin_response_sent_outbox", phone=phone_number)
                else:
                    # Legacy String Response
                    await self.whatsapp_client.send_text(phone_number, response)
                    logger.info("pin_response_sent_legacy", phone=phone_number, flow_type=flow_type)

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
