"""Airtime confirmation step."""

import hashlib
import json
from typing import Any

from apps.core.src.agent.graphs.airtime.models.types import (
    AirtimeContext,
    AirtimeGates,
    AirtimePayload,
)
from apps.core.src.agent.graphs.airtime.pipeline.base import AirtimeStep
from apps.core.src.agent.orchestrator.models.domain import TransactionOutcome, TransactionResult
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class ConfirmationStep(AirtimeStep):
    """Generates confirmation snapshot."""

    async def execute(
        self,
        data: AirtimePayload,
        context: AirtimeContext,
        gates: AirtimeGates,
        worker_context: Any,
    ) -> TransactionResult:
        
        snapshot = {
            "amount": data.amount,
            "recipient_phone": data.recipient_phone,
            "network": data.network,
            "source_account": data.source_account_number,
        }
        
        snapshot_str = json.dumps(snapshot, sort_keys=True)
        snapshot_hash = hashlib.sha256(snapshot_str.encode()).hexdigest()
        
        current_hash = data.confirmation.snapshot_hash if hasattr(data, "confirmation") else None
        
        if gates.confirmation_confirmed and current_hash == snapshot_hash:
            return TransactionResult(outcome=TransactionOutcome.OK)
            
        summary = (
            f"Buy ₦{data.amount:,.2f} {data.network} Airtime for {data.recipient_phone}?"
        )
        
        return TransactionResult(
            outcome=TransactionOutcome.NEEDS_CONFIRMATION,
            confirmation_summary=summary,
            confirmation_snapshot=snapshot,
            update_message=summary
        )
