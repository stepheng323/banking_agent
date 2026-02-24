"""Transaction resolver for support queries.

Resolves which transaction the user is referring to using:
1. Quoted message → hydrate from ActionableMessage
2. Explicit reference (amount + date + recipient)
3. Most recent unresolved transaction
4. Ambiguous → ask clarification
"""

from datetime import date, datetime, timedelta
from typing import Any

from apps.core.src.agent.graphs.support.models import TransactionReference
from shared.database.models import Transaction
from shared.repositories.actionable_message_repository import ActionableMessageRepository
from shared.repositories.transaction_repository import TransactionRepository
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionResolver:
    """Resolves transaction references from support queries."""

    def __init__(
        self,
        transaction_repo: TransactionRepository,
        actionable_message_repo: ActionableMessageRepository,
    ):
        self.tx_repo = transaction_repo
        self.am_repo = actionable_message_repo

    async def resolve(
        self,
        user_id: str,
        tx_ref: TransactionReference | None,
        quoted_message_id: str | None = None,
    ) -> tuple[Transaction | None, str]:
        """
        Resolve a transaction reference.

        Returns:
            (transaction, resolution_method) where method is one of:
            "quoted", "explicit", "recent", "ambiguous", "not_found"
        """
        if quoted_message_id:
            tx = await self._resolve_from_quoted(quoted_message_id, user_id)
            if tx:
                logger.info("transaction_resolved", method="quoted", tx_id=str(tx.id))
                return tx, "quoted"

        if tx_ref and self._has_explicit_ref(tx_ref):
            tx = await self._resolve_from_explicit(user_id, tx_ref)
            if tx:
                logger.info("transaction_resolved", method="explicit", tx_id=str(tx.id))
                return tx, "explicit"

        tx = await self._resolve_from_recent(user_id)
        if tx:
            logger.info("transaction_resolved", method="recent", tx_id=str(tx.id))
            return tx, "recent"

        logger.info("transaction_not_resolved", user_id=user_id)
        return None, "not_found"

    async def _resolve_from_quoted(self, quoted_message_id: str, user_id: str) -> Transaction | None:
        """Resolve transaction from quoted ActionableMessage.

        Security: Only resolves if message is owned by user.
        """
        try:
            am = self.am_repo.get_by_channel_message_id_for_user(quoted_message_id, user_id)
            if not am:
                return None

            tx_id = am.message_data.get("transaction_id")
            if not tx_id:
                return None

            return self.tx_repo.get_by_id(tx_id)
        except Exception as e:
            logger.warning("quoted_resolution_failed", error=str(e))
            return None

    async def _resolve_from_explicit(self, user_id: str, tx_ref: TransactionReference) -> Transaction | None:
        """Resolve transaction from explicit reference."""
        try:
            transactions = self.tx_repo.get_by_user(user_id, limit=50)

            candidates = []
            for tx in transactions:
                score = self._match_score(tx, tx_ref)
                if score > 0:
                    candidates.append((tx, score))

            if not candidates:
                return None

            # Sort by score descending
            candidates.sort(key=lambda x: x[1], reverse=True)

            # If top candidate has good score and is significantly better than second
            if len(candidates) == 1 or candidates[0][1] > candidates[1][1] * 1.5:
                return candidates[0][0]

            # Ambiguous - multiple good matches
            return None

        except Exception as e:
            logger.warning("explicit_resolution_failed", error=str(e))
            return None

    async def _resolve_from_recent(self, user_id: str) -> Transaction | None:
        """Get most recent unresolved (pending/failed) transaction."""
        try:
            pending = self.tx_repo.get_by_status(user_id, "pending")
            if pending:
                return pending[0]
            failed = self.tx_repo.get_by_status(user_id, "failed")
            if failed:
                return failed[0]

            recent = self.tx_repo.get_by_user(user_id, limit=1)
            return recent[0] if recent else None

        except Exception as e:
            logger.warning("recent_resolution_failed", error=str(e))
            return None

    def _has_explicit_ref(self, tx_ref: TransactionReference) -> bool:
        """Check if reference has explicit identifiers."""
        return bool(tx_ref.amount or tx_ref.recipient_name or tx_ref.date_hint)

    def _match_score(self, tx: Transaction, tx_ref: TransactionReference) -> float:
        """Calculate match score between transaction and reference."""
        score = 0.0

        if tx_ref.amount:
            if abs(tx.amount - tx_ref.amount) < 1:  # Exact match
                score += 3.0
            elif abs(tx.amount - tx_ref.amount) / tx_ref.amount < 0.05:  # Within 5%
                score += 1.0

        if tx_ref.recipient_name and tx.recipient_name:
            if tx_ref.recipient_name.lower() in tx.recipient_name.lower():
                score += 2.0
        if tx_ref.date_hint:
            tx_date = tx.created_at.date() if tx.created_at else None
            target_date = self._parse_date_hint(tx_ref.date_hint)
            if tx_date and target_date:
                days_diff = abs((tx_date - target_date).days)
                if days_diff == 0:
                    score += 2.0
                elif days_diff <= 1:
                    score += 1.0

        return score

    def _parse_date_hint(self, hint: str) -> date | None:
        """Parse date hint to actual date."""
        hint_lower = hint.lower().strip()
        today = date.today()

        if hint_lower in ("today", "now"):
            return today
        elif hint_lower == "yesterday":
            return today - timedelta(days=1)
        elif hint_lower == "last week":
            return today - timedelta(days=7)

        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(hint, fmt).date()
            except ValueError:
                continue

        return None

    def transaction_to_dict(self, tx: Transaction) -> dict[str, Any]:
        """Convert transaction to dictionary for handlers."""
        return {
            "id": str(tx.id),
            "transaction_id": tx.transaction_id,
            "status": tx.status,
            "amount": tx.amount,
            "currency": tx.currency,
            "recipient_name": tx.recipient_name,
            "recipient_account_number": tx.recipient_account_number,
            "recipient_bank_name": tx.recipient_bank_name,
            "source_bank_name": tx.source_bank_name,
            "narration": tx.narration,
            "error_message": tx.error_message,
            "provider_response": tx.provider_response or {},
            "provider_status": getattr(tx, "provider_status", None),
            "provider_error_code": getattr(tx, "provider_error_code", None),
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
            "completed_at": tx.completed_at.isoformat() if tx.completed_at else None,
        }
