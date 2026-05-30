"""Transaction resolver for support queries.

Resolves which transaction the user is referring to using:
1. Quoted message → hydrate from ActionableMessage
2. Explicit reference (amount + date + recipient)
3. Most recent unresolved transaction
4. Ambiguous → ask clarification
"""

from datetime import date, datetime, timedelta
from typing import Any

from apps.chat.src.agent.shared.unified_transactions import (
    UnifiedTransactionRecord,
    UnifiedTransactionService,
    default_support_window,
)
from apps.chat.src.agent.workers.support.handlers.status_utils import normalize_transaction_status
from apps.chat.src.agent.workers.support.models import TransactionReference
from banking.messaging.repositories.actionable_message_repository import ActionableMessageRepository
from banking.transactions.repositories.bank_transaction_repository import BankTransactionRepository
from banking.transactions.repositories.transaction_repository import TransactionRepository
from shared.config.settings import settings
from shared.database.models import Transaction
from shared.utils.logging import get_logger

logger = get_logger(__name__)


class TransactionResolver:
    """Resolves transaction references from support queries."""

    def __init__(
        self,
        transaction_repo: TransactionRepository,
        actionable_message_repo: ActionableMessageRepository,
        bank_transaction_repo: BankTransactionRepository | None = None,
    ):
        self.tx_repo = transaction_repo
        self.am_repo = actionable_message_repo
        self.unified_service = UnifiedTransactionService(
            transaction_repo,
            bank_transaction_repo,
            load_bank_rows_from_uow=False,
        )

    async def resolve(
        self,
        user_id: str,
        tx_ref: TransactionReference | None,
        quoted_message_id: str | None = None,
        recent_status_priority: list[str] | None = None,
        prefer_latest_recent: bool = False,
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

        if tx_ref and tx_ref.transaction_id:
            tx = await self.tx_repo.get_by_id(tx_ref.transaction_id)
            if tx:
                logger.info("transaction_resolved", method="transaction_id", tx_id=str(tx.id))
                return tx, "transaction_id"

        if settings.enable_unified_transaction_view:
            tx, method = await self._resolve_from_unified(
                user_id,
                tx_ref,
                recent_status_priority=recent_status_priority,
                prefer_latest=prefer_latest_recent,
            )
            if tx:
                logger.info("transaction_resolved", method=method, tx_id=str(tx.get("id") or tx.get("transaction_id")))
                return tx, method
            if method == "ambiguous":
                return None, method

        if tx_ref and self._has_explicit_ref(tx_ref):
            tx = await self._resolve_from_explicit(user_id, tx_ref)
            if tx:
                logger.info("transaction_resolved", method="explicit", tx_id=str(tx.id))
                return tx, "explicit"

        tx = await self._resolve_from_recent(
            user_id,
            status_priority=recent_status_priority,
            prefer_latest=prefer_latest_recent,
        )
        if tx:
            logger.info("transaction_resolved", method="recent", tx_id=str(tx.id))
            return tx, "recent"

        logger.info("transaction_not_resolved", user_id=user_id)
        return None, "not_found"

    async def _resolve_from_unified(
        self,
        user_id: str,
        tx_ref: TransactionReference | None,
        *,
        recent_status_priority: list[str] | None,
        prefer_latest: bool,
    ) -> tuple[dict[str, Any] | None, str]:
        start_date, end_date = self._unified_window(tx_ref)
        try:
            records = await self.unified_service.list_for_user(
                user_id,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            logger.warning("unified_resolution_failed", error=str(exc))
            return None, "not_found"

        if not records:
            return None, "not_found"

        if tx_ref and tx_ref.transaction_id:
            target = tx_ref.transaction_id.strip()
            for record in records:
                data = record.to_support_dict()
                identifiers = {
                    str(data.get("id") or ""),
                    str(data.get("transaction_id") or ""),
                    str(data.get("local_transaction_id") or ""),
                    str(data.get("bank_transaction_id") or ""),
                    str(data.get("provider_reference") or ""),
                }
                if target in identifiers:
                    return data, "transaction_id"

        if tx_ref and self._has_explicit_ref(tx_ref):
            candidates: list[tuple[UnifiedTransactionRecord, float]] = []
            for record in records:
                score = self._unified_match_score(record, tx_ref)
                if score > 0:
                    candidates.append((record, score))
            if not candidates:
                return None, "not_found"
            candidates.sort(key=lambda item: item[1], reverse=True)
            if len(candidates) == 1 or candidates[0][1] > candidates[1][1] * 1.5:
                return candidates[0][0].to_support_dict(), "explicit"
            return None, "ambiguous"

        if prefer_latest:
            return records[0].to_support_dict(), "recent"

        if recent_status_priority:
            priority = [normalize_transaction_status(status) for status in recent_status_priority]
            priority = [status for status in priority if status != "unknown"]
            for status in priority:
                for record in records:
                    if normalize_transaction_status(record.display_status) == status:
                        return record.to_support_dict(), "recent"

        for status in ("pending", "processing", "failed"):
            for record in records:
                if normalize_transaction_status(record.display_status) == status:
                    return record.to_support_dict(), "recent"

        return records[0].to_support_dict(), "recent"

    def _unified_window(self, tx_ref: TransactionReference | None) -> tuple[date, date]:
        if tx_ref and tx_ref.date_hint:
            parsed = self._parse_date_hint(tx_ref.date_hint)
            if parsed is not None:
                return parsed - timedelta(days=1), parsed + timedelta(days=1)
        return default_support_window()

    def _unified_match_score(self, record: UnifiedTransactionRecord, tx_ref: TransactionReference) -> float:
        data = record.to_support_dict()
        score = 0.0

        if tx_ref.amount:
            amount = float(data.get("amount") or 0.0)
            if abs(amount - tx_ref.amount) < 1:
                score += 3.0
            elif abs(amount - tx_ref.amount) / tx_ref.amount < 0.05:
                score += 1.0

        if tx_ref.recipient_name:
            target = tx_ref.recipient_name.lower()
            haystack = " ".join(
                str(data.get(key) or "")
                for key in ("recipient_name", "counterparty", "narration", "recipient_bank_name")
            ).lower()
            if target in haystack:
                score += 2.0

        if tx_ref.date_hint:
            target_date = self._parse_date_hint(tx_ref.date_hint)
            if target_date:
                raw_date = data.get("created_at") or data.get("date")
                tx_date = None
                if raw_date:
                    try:
                        tx_date = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
                    except ValueError:
                        tx_date = None
                if tx_date:
                    days_diff = abs((tx_date - target_date).days)
                    if days_diff == 0:
                        score += 2.0
                    elif days_diff <= 1:
                        score += 1.0

        return score

    async def _resolve_from_quoted(self, quoted_message_id: str, user_id: str) -> Transaction | None:
        """Resolve transaction from quoted ActionableMessage.

        Security: Only resolves if message is owned by user.
        """
        try:
            am = await self.am_repo.get_by_channel_message_id_for_user(quoted_message_id, user_id)
            if not am:
                return None

            tx_id = am.message_data.get("transaction_id") or am.message_data.get("idempotency_key")
            if not tx_id:
                return None

            tx = await self.tx_repo.get_by_id(tx_id)
            if tx:
                return tx
            return await self.tx_repo.get_by_idempotency_key(tx_id)
        except Exception as e:
            logger.warning("quoted_resolution_failed", error=str(e))
            return None

    async def _resolve_from_explicit(self, user_id: str, tx_ref: TransactionReference) -> Transaction | None:
        """Resolve transaction from explicit reference."""
        try:
            transactions = await self.tx_repo.get_by_user(user_id, limit=50)

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

    async def _resolve_from_recent(
        self,
        user_id: str,
        *,
        status_priority: list[str] | None = None,
        prefer_latest: bool = False,
    ) -> Transaction | None:
        """Get most recent unresolved (pending/failed) transaction."""
        try:
            if prefer_latest:
                recent = await self.tx_repo.get_by_user(user_id, limit=1)
                return recent[0] if recent else None

            if status_priority:
                priority = [normalize_transaction_status(status) for status in status_priority]
                priority = [status for status in priority if status != "unknown"]
                if priority:
                    exact_primary = await self.tx_repo.get_by_status(user_id, priority[0])
                    if exact_primary:
                        return exact_primary[0]

                    recent = await self.tx_repo.get_by_user(user_id, limit=50)
                    if recent_match := self._pick_by_status_priority(recent, priority):
                        return recent_match

                    for status in priority[1:]:
                        exact_matches = await self.tx_repo.get_by_status(user_id, status)
                        if exact_matches:
                            return exact_matches[0]

                    return recent[0] if recent else None

            pending = await self.tx_repo.get_by_status(user_id, "pending")
            if pending:
                return pending[0]
            failed = await self.tx_repo.get_by_status(user_id, "failed")
            if failed:
                return failed[0]

            recent = await self.tx_repo.get_by_user(user_id, limit=1)
            return recent[0] if recent else None

        except Exception as e:
            logger.warning("recent_resolution_failed", error=str(e))
            return None

    @staticmethod
    def _tx_attr(tx: Any, key: str) -> Any:
        if isinstance(tx, dict):
            return tx.get(key)
        return getattr(tx, key, None)

    @classmethod
    def _tx_status(cls, tx: Any) -> str:
        for key in ("status", "final_status", "provider_status", "transaction_status", "tx_status"):
            status = normalize_transaction_status(cls._tx_attr(tx, key))
            if status != "unknown":
                return status
        provider_response = cls._tx_attr(tx, "provider_response")
        if isinstance(provider_response, dict):
            for key in ("status", "provider_status", "transaction_status", "tx_status"):
                status = normalize_transaction_status(provider_response.get(key))
                if status != "unknown":
                    return status
        return "unknown"

    @classmethod
    def _pick_by_status_priority(cls, transactions: list[Any], priority: list[str]) -> Any | None:
        for status in priority:
            for tx in transactions:
                if cls._tx_status(tx) == status:
                    return tx
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

        recipient_name = self._display_recipient(tx)
        if tx_ref.recipient_name and recipient_name:
            if tx_ref.recipient_name.lower() in recipient_name.lower():
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

    @staticmethod
    def _display_recipient(tx: Transaction) -> str | None:
        transaction_type = str(getattr(tx, "transaction_type", "") or "").strip().lower()
        target_phone = str(getattr(tx, "target_phone_number", "") or "").strip()
        mobile_network = str(getattr(tx, "mobile_network", "") or "").strip()
        biller_item_name = str(getattr(tx, "biller_item_name", "") or "").strip()
        recipient_name = str(getattr(tx, "recipient_name", "") or "").strip()
        recipient_account = str(getattr(tx, "recipient_account_number", "") or "").strip()
        recipient_bank = str(getattr(tx, "recipient_bank_name", "") or "").strip()

        if transaction_type == "airtime":
            phone = target_phone or recipient_account
            network = mobile_network or recipient_bank
            if phone and network:
                return f"{phone} ({network})"
            return phone or network or recipient_name or None
        if transaction_type == "data":
            plan = biller_item_name or recipient_name
            phone = target_phone or recipient_account
            if plan and phone:
                return f"{plan} for {phone}"
            return plan or phone or mobile_network or recipient_name or None
        return recipient_name or None

    def transaction_to_dict(self, tx: Transaction) -> dict[str, Any]:
        """Convert transaction to dictionary for handlers."""
        if isinstance(tx, dict):
            return dict(tx)
        recipient_name = self._display_recipient(tx)
        return {
            "id": str(tx.id),
            "transaction_type": tx.transaction_type,
            "transaction_id": tx.transaction_id,
            "status": tx.status,
            "amount": tx.amount,
            "currency": tx.currency,
            "recipient_name": recipient_name,
            "recipient_account_number": tx.recipient_account_number,
            "recipient_bank_code": tx.recipient_bank_code,
            "recipient_bank_name": tx.recipient_bank_name,
            "target_phone_number": getattr(tx, "target_phone_number", None),
            "mobile_network": getattr(tx, "mobile_network", None),
            "biller_code": getattr(tx, "biller_code", None),
            "biller_item_code": getattr(tx, "biller_item_code", None),
            "biller_item_name": getattr(tx, "biller_item_name", None),
            "service_metadata": getattr(tx, "service_metadata", None) or {},
            "source_bank_name": tx.source_bank_name,
            "narration": tx.narration,
            "error_message": tx.error_message,
            "failure_category": getattr(tx, "failure_category", None),
            "provider_response": tx.provider_response or {},
            "provider_status": getattr(tx, "provider_status", None),
            "provider_error_code": getattr(tx, "provider_error_code", None),
            "created_at": tx.created_at.isoformat() if tx.created_at else None,
            "completed_at": tx.completed_at.isoformat() if tx.completed_at else None,
        }
