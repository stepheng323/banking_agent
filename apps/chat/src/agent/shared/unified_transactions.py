"""Read-side unified transaction records for query and support."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

UnifiedSource = Literal["local", "bank", "reconciled"]
MatchConfidence = Literal["exact", "strong", "none"]


def normalize_transaction_status(status: Any) -> str:
    """Map provider/database status variants into support-facing states."""
    raw = getattr(status, "value", status)
    normalized = str(raw or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    if "." in normalized:
        normalized = normalized.rsplit(".", 1)[-1]

    if normalized in {"success", "successful", "complete", "completed", "confirmed"}:
        return "successful"
    if normalized in {"pending", "processing", "queued", "initiated", "in_progress"}:
        return "processing" if normalized != "pending" else "pending"
    if normalized in {
        "fail",
        "failed",
        "failure",
        "error",
        "errored",
        "declined",
        "rejected",
        "failed_transfer",
        "transfer_failed",
    }:
        return "failed"
    if normalized in {"reversed", "refunded"}:
        return "reversed"
    return normalized or "unknown"


@dataclass
class UnifiedTransactionRecord:
    """Normalized read model for local app and bank-history transactions."""

    source: UnifiedSource
    amount: float
    currency: str
    type: str
    transaction_type: str
    effective_at: datetime
    counterparty: str | None = None
    bank_name: str | None = None
    local_transaction_id: str | None = None
    bank_transaction_id: str | None = None
    provider_reference: str | None = None
    local_status: str | None = None
    bank_status: str | None = None
    display_status: str = "unknown"
    match_confidence: MatchConfidence = "none"
    needs_review: bool = False
    actionable: dict[str, bool] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_query_dict(self) -> dict[str, Any]:
        """Return the dict shape consumed by query filters and presentation."""
        identifier = self.provider_reference or self.local_transaction_id or self.bank_transaction_id or ""
        narration = str(self.metadata.get("narration") or "").strip()
        counterparty = self.counterparty or str(self.metadata.get("recipient_name") or "").strip() or None
        if not narration:
            narration = counterparty or "Transaction"
        return {
            "id": identifier,
            "transaction_id": self.provider_reference or identifier,
            "type": self.type,
            "transaction_type": self.transaction_type,
            "amount": self.amount,
            "currency": self.currency,
            "narration": narration,
            "date": self.effective_at.isoformat(),
            "status": self.display_status,
            "local_status": self.local_status,
            "bank_status": self.bank_status,
            "display_status": self.display_status,
            "unified_source": self.source,
            "source": self.source,
            "local_transaction_id": self.local_transaction_id,
            "bank_transaction_id": self.bank_transaction_id,
            "provider_reference": self.provider_reference,
            "match_confidence": self.match_confidence,
            "needs_review": self.needs_review,
            "actionable": dict(self.actionable),
            "counterparty": counterparty,
            "recipient_name": self.metadata.get("recipient_name") or counterparty,
            "recipient_account_number": self.metadata.get("recipient_account_number"),
            "recipient_bank_name": self.metadata.get("recipient_bank_name"),
            "recipient_bank_code": self.metadata.get("recipient_bank_code"),
            "target_phone_number": self.metadata.get("target_phone_number"),
            "mobile_network": self.metadata.get("mobile_network"),
            "biller_code": self.metadata.get("biller_code"),
            "biller_item_code": self.metadata.get("biller_item_code"),
            "biller_item_name": self.metadata.get("biller_item_name"),
            "service_metadata": self.metadata.get("service_metadata") or {},
            "source_account_id": self.metadata.get("source_account_id"),
            "source_account_number": self.metadata.get("source_account_number"),
            "source_account_label": self.metadata.get("source_account_label") or self.bank_name,
            "source_bank_name": self.metadata.get("source_bank_name") or self.bank_name,
            "bank_name": self.bank_name or "",
            "category": self.metadata.get("category"),
            "resolved_category": self.metadata.get("resolved_category"),
            "counterparty_role": self.metadata.get("counterparty_role"),
            "counterparty_source": self.metadata.get("counterparty_source"),
            "parser_rule": self.metadata.get("parser_rule"),
            "provider_response": self.metadata.get("provider_response") or {},
            "error_message": self.metadata.get("error_message"),
            "failure_category": self.metadata.get("failure_category"),
            "provider_status": self.metadata.get("provider_status"),
            "provider_error_code": self.metadata.get("provider_error_code"),
            "created_at": self.metadata.get("created_at"),
            "completed_at": self.metadata.get("completed_at"),
        }

    def to_support_dict(self) -> dict[str, Any]:
        """Return a support-handler transaction dict."""
        data = self.to_query_dict()
        data["id"] = self.local_transaction_id or self.bank_transaction_id or data.get("id")
        data["transaction_id"] = self.provider_reference or self.local_transaction_id or self.bank_transaction_id
        data["status"] = self.display_status
        data["unified_source"] = self.source
        return data


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _tx_attr(tx: Any, key: str) -> Any:
    if isinstance(tx, dict):
        return tx.get(key)
    return getattr(tx, key, None)


def _provider_response(tx: Any) -> dict[str, Any]:
    response = _tx_attr(tx, "provider_response") or _tx_attr(tx, "raw_payload") or {}
    return response if isinstance(response, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm_text(value: Any) -> str:
    return " ".join(_clean(value).lower().replace("_", " ").replace("-", " ").split())


def _reference_values(tx: Any) -> set[str]:
    response = _provider_response(tx)
    values: set[str] = set()
    for key in (
        "transaction_id",
        "provider_transaction_id",
        "provider_reference",
        "reference",
        "ref",
        "debit_id",
        "idempotency_key",
    ):
        for source in (tx, response):
            value = _clean(_tx_attr(source, key) if not isinstance(source, dict) else source.get(key))
            if value:
                values.add(value)
    return values


def _first_reference(tx: Any) -> str | None:
    for value in _reference_values(tx):
        return value
    return None


def _local_effective_at(tx: Any) -> datetime:
    return (
        _coerce_datetime(_tx_attr(tx, "completed_at"))
        or _coerce_datetime(_tx_attr(tx, "updated_at"))
        or _coerce_datetime(_tx_attr(tx, "created_at"))
        or datetime.now(UTC).replace(tzinfo=None)
    )


def _bank_effective_at(tx: Any) -> datetime:
    return (
        _coerce_datetime(_tx_attr(tx, "posted_at"))
        or _coerce_datetime(_tx_attr(tx, "date"))
        or _coerce_datetime(_tx_attr(tx, "posted_date"))
        or datetime.now(UTC).replace(tzinfo=None)
    )


def _service_metadata(tx: Any) -> dict[str, Any]:
    raw = _tx_attr(tx, "service_metadata")
    return raw if isinstance(raw, dict) else {}


def _mobile_target_display(name: str, phone: str, network: str) -> str:
    if name and phone and network:
        return f"{name} ({phone}, {network})"
    if name and phone:
        return f"{name} ({phone})"
    if name and network:
        return f"{name} ({network})"
    if name:
        return name
    if phone and network:
        return f"{phone} ({network})"
    return phone or network


def _local_mobile_values(tx: Any, transaction_type: str) -> dict[str, str | None]:
    service_metadata = _service_metadata(tx)
    target_phone = _clean(_tx_attr(tx, "target_phone_number"))
    mobile_network = _clean(_tx_attr(tx, "mobile_network"))
    biller_item_name = _clean(_tx_attr(tx, "biller_item_name"))

    if transaction_type == "airtime":
        recipient_name = _clean(service_metadata.get("recipient_name"))
        counterparty = _mobile_target_display(recipient_name, target_phone, mobile_network)
        return {
            "counterparty": counterparty or recipient_name or None,
            "recipient_name": recipient_name or counterparty or None,
            "target_phone_number": target_phone or None,
            "mobile_network": mobile_network or None,
            "biller_item_name": None,
        }

    if transaction_type == "data":
        plan_name = biller_item_name
        if not plan_name:
            plan_name = _clean(service_metadata.get("plan_name"))
        target_name = _clean(service_metadata.get("recipient_name"))
        target_display = (
            _mobile_target_display(target_name, target_phone, mobile_network) if target_name else target_phone
        )
        counterparty = plan_name
        if target_display:
            counterparty = f"{plan_name} for {target_display}" if plan_name else target_display
        return {
            "counterparty": counterparty or None,
            "recipient_name": counterparty or plan_name or target_phone or None,
            "target_phone_number": target_phone or None,
            "mobile_network": mobile_network or None,
            "biller_item_name": plan_name or None,
        }

    return {
        "counterparty": None,
        "recipient_name": None,
        "target_phone_number": None,
        "mobile_network": None,
        "biller_item_name": biller_item_name or None,
    }


def _local_to_record(tx: Any) -> UnifiedTransactionRecord:
    status = normalize_transaction_status(_tx_attr(tx, "status"))
    provider_status = normalize_transaction_status(_tx_attr(tx, "provider_status"))
    if status == "unknown" and provider_status != "unknown":
        status = provider_status
    transaction_type = _clean(_tx_attr(tx, "transaction_type")) or "transfer"
    is_mobile_transaction = transaction_type in {"airtime", "data"}
    recipient = "" if is_mobile_transaction else _clean(_tx_attr(tx, "recipient_name"))
    mobile_values = _local_mobile_values(tx, transaction_type)
    display_recipient = str(mobile_values.get("recipient_name") or recipient or "").strip()
    counterparty = str(mobile_values.get("counterparty") or recipient or "").strip()
    amount = abs(float(_tx_attr(tx, "amount") or 0.0))
    provider_reference = _first_reference(tx)
    tx_id = _clean(_tx_attr(tx, "id"))
    created_at = _coerce_datetime(_tx_attr(tx, "created_at"))
    completed_at = _coerce_datetime(_tx_attr(tx, "completed_at"))
    actionable = {
        "retry": transaction_type == "transfer" and status == "failed",
        "receipt": transaction_type == "transfer" and status == "successful",
        "escalate": True,
    }
    return UnifiedTransactionRecord(
        source="local",
        local_transaction_id=tx_id or None,
        provider_reference=provider_reference,
        amount=amount,
        currency=_clean(_tx_attr(tx, "currency")) or "NGN",
        type="debit",
        transaction_type=transaction_type,
        counterparty=counterparty or None,
        bank_name=_clean(_tx_attr(tx, "source_bank_name")) or _clean(_tx_attr(tx, "recipient_bank_name")) or None,
        effective_at=_local_effective_at(tx),
        local_status=status,
        display_status=status,
        match_confidence="none",
        actionable=actionable,
        metadata={
            "narration": _tx_attr(tx, "narration"),
            "recipient_name": display_recipient or None,
            "recipient_account_number": None if is_mobile_transaction else _tx_attr(tx, "recipient_account_number"),
            "recipient_bank_name": None if is_mobile_transaction else _tx_attr(tx, "recipient_bank_name"),
            "recipient_bank_code": None if is_mobile_transaction else _tx_attr(tx, "recipient_bank_code"),
            "target_phone_number": mobile_values.get("target_phone_number"),
            "mobile_network": mobile_values.get("mobile_network"),
            "biller_code": _tx_attr(tx, "biller_code"),
            "biller_item_code": _tx_attr(tx, "biller_item_code"),
            "biller_item_name": mobile_values.get("biller_item_name") or _tx_attr(tx, "biller_item_name"),
            "service_metadata": _service_metadata(tx),
            "source_account_id": _clean(_tx_attr(tx, "source_account_id")) or None,
            "source_account_number": _tx_attr(tx, "source_account_number"),
            "source_bank_name": _tx_attr(tx, "source_bank_name"),
            "provider_response": _provider_response(tx),
            "provider_status": _tx_attr(tx, "provider_status"),
            "provider_error_code": _tx_attr(tx, "provider_error_code"),
            "error_message": _tx_attr(tx, "error_message"),
            "failure_category": _tx_attr(tx, "failure_category"),
            "created_at": created_at.isoformat() if created_at else None,
            "completed_at": completed_at.isoformat() if completed_at else None,
        },
    )


def _bank_to_record(tx: Any) -> UnifiedTransactionRecord:
    amount = abs(float(_tx_attr(tx, "amount") or 0.0))
    tx_type = _clean(_tx_attr(tx, "type") or _tx_attr(tx, "transaction_type")) or "debit"
    narration = _clean(_tx_attr(tx, "narration"))
    counterparty = _clean(_tx_attr(tx, "counterparty"))
    bank_id = _clean(
        _tx_attr(tx, "provider_transaction_id")
        or _tx_attr(tx, "transaction_id")
        or _tx_attr(tx, "id")
    )
    bank_name = _clean(_tx_attr(tx, "bank_name") or _tx_attr(tx, "source_account_label"))
    return UnifiedTransactionRecord(
        source="bank",
        bank_transaction_id=bank_id or None,
        provider_reference=bank_id or None,
        amount=amount,
        currency=_clean(_tx_attr(tx, "currency")) or "NGN",
        type=tx_type,
        transaction_type=tx_type,
        counterparty=counterparty or None,
        bank_name=bank_name or None,
        effective_at=_bank_effective_at(tx),
        bank_status="posted",
        display_status="posted",
        match_confidence="none",
        actionable={"retry": False, "receipt": False, "escalate": True},
        metadata={
            "narration": narration,
            "category": _tx_attr(tx, "category"),
            "resolved_category": _tx_attr(tx, "resolved_category"),
            "counterparty_role": _tx_attr(tx, "counterparty_role"),
            "counterparty_source": _tx_attr(tx, "counterparty_source"),
            "parser_rule": _tx_attr(tx, "parser_rule"),
            "source_account_id": _clean(_tx_attr(tx, "source_account_id") or _tx_attr(tx, "linked_account_id")) or None,
            "source_account_label": _tx_attr(tx, "source_account_label") or bank_name,
            "source_bank_name": bank_name or None,
            "provider_response": _provider_response(tx),
            "created_at": _bank_effective_at(tx).isoformat(),
        },
    )


def _display_status(local_status: str | None, bank_status: str | None) -> tuple[str, bool]:
    if bank_status == "posted" and local_status == "successful":
        return "successful", False
    if bank_status == "posted" and local_status in {"pending", "processing"}:
        return "processing", False
    if bank_status == "posted" and local_status == "failed":
        return "failed", True
    if local_status and local_status != "unknown":
        return local_status, False
    if bank_status:
        return bank_status, False
    return "unknown", False


def _merge_records(
    local: UnifiedTransactionRecord,
    bank: UnifiedTransactionRecord,
    *,
    confidence: MatchConfidence,
) -> UnifiedTransactionRecord:
    display_status, needs_review = _display_status(local.local_status, bank.bank_status)
    actionable = {
        "retry": bool(local.actionable.get("retry")) and not needs_review,
        "receipt": bool(local.actionable.get("receipt")),
        "escalate": True,
    }
    metadata = dict(bank.metadata)
    metadata.update({key: value for key, value in local.metadata.items() if value not in (None, "")})
    return UnifiedTransactionRecord(
        source="reconciled",
        local_transaction_id=local.local_transaction_id,
        bank_transaction_id=bank.bank_transaction_id,
        provider_reference=local.provider_reference or bank.provider_reference,
        amount=local.amount or bank.amount,
        currency=local.currency or bank.currency,
        type=local.type or bank.type,
        transaction_type=local.transaction_type or bank.transaction_type,
        counterparty=local.counterparty or bank.counterparty,
        bank_name=local.bank_name or bank.bank_name,
        effective_at=max(local.effective_at, bank.effective_at),
        local_status=local.local_status,
        bank_status=bank.bank_status,
        display_status=display_status,
        match_confidence=confidence,
        needs_review=needs_review,
        actionable=actionable,
        metadata=metadata,
    )


def _texts_compatible(left: str | None, right: str | None) -> bool:
    left_norm = _norm_text(left)
    right_norm = _norm_text(right)
    if not left_norm or not right_norm:
        return True
    return left_norm in right_norm or right_norm in left_norm


def _strong_match(local: UnifiedTransactionRecord, bank: UnifiedTransactionRecord) -> bool:
    if local.type != "debit" or bank.type != "debit":
        return False
    if abs(local.amount - bank.amount) >= 1:
        return False
    if abs((local.effective_at.date() - bank.effective_at.date()).days) > 1:
        return False
    if not _texts_compatible(local.bank_name, bank.bank_name):
        return False
    bank_text = bank.counterparty or bank.metadata.get("narration")
    return _texts_compatible(local.counterparty, str(bank_text or ""))


def _in_window(record: UnifiedTransactionRecord, start_date: date, end_date: date) -> bool:
    return start_date <= record.effective_at.date() <= end_date


class UnifiedTransactionService:
    """Build read-side unified transaction records."""

    def __init__(
        self,
        transaction_repo: Any | None = None,
        bank_transaction_repo: Any | None = None,
        *,
        load_bank_rows_from_uow: bool = True,
    ) -> None:
        self.transaction_repo = transaction_repo
        self.bank_transaction_repo = bank_transaction_repo
        self.load_bank_rows_from_uow = load_bank_rows_from_uow

    async def list_for_user(
        self,
        user_id: str,
        *,
        start_date: date,
        end_date: date,
        bank_transactions: list[Any] | None = None,
        local_limit: int = 200,
    ) -> list[UnifiedTransactionRecord]:
        local_rows = await self._load_local_rows(user_id, start_date=start_date, end_date=end_date, limit=local_limit)
        bank_rows = (
            list(bank_transactions)
            if bank_transactions is not None
            else await self._load_bank_rows(user_id, start_date=start_date, end_date=end_date)
        )
        records = self.reconcile(local_rows, bank_rows)
        return [record for record in records if _in_window(record, start_date, end_date)]

    async def _load_local_rows(self, user_id: str, *, start_date: date, end_date: date, limit: int) -> list[Any]:
        transaction_repo = self.transaction_repo
        if transaction_repo is None:
            from banking.persistence.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                if uow.transactions is None:
                    return []
                return await uow.transactions.list_by_user_window(
                    user_id,
                    start_date=start_date,
                    end_date=end_date,
                    limit=limit,
                )

        assert transaction_repo is not None
        if hasattr(transaction_repo, "list_by_user_window"):
            return await transaction_repo.list_by_user_window(
                user_id,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )

        rows = await transaction_repo.get_by_user(user_id, limit=limit)
        return [
            row
            for row in rows
            if start_date <= _local_effective_at(row).date() <= end_date
        ]

    async def _load_bank_rows(self, user_id: str, *, start_date: date, end_date: date) -> list[Any]:
        bank_transaction_repo = self.bank_transaction_repo
        if bank_transaction_repo is None or not hasattr(bank_transaction_repo, "list_by_user_window"):
            if not self.load_bank_rows_from_uow:
                return []
            from banking.persistence.unit_of_work import UnitOfWork

            async with UnitOfWork() as uow:
                if uow.bank_transactions is None:
                    return []
                return await uow.bank_transactions.list_by_user_window(
                    user_id,
                    start_date=start_date,
                    end_date=end_date,
                )

        assert bank_transaction_repo is not None
        return await bank_transaction_repo.list_by_user_window(
            user_id,
            start_date=start_date,
            end_date=end_date,
        )

    @classmethod
    def reconcile(cls, local_transactions: list[Any], bank_transactions: list[Any]) -> list[UnifiedTransactionRecord]:
        local_records = [_local_to_record(tx) for tx in local_transactions]
        bank_records = [_bank_to_record(tx) for tx in bank_transactions]
        used_bank_indexes: set[int] = set()
        results: list[UnifiedTransactionRecord] = []

        bank_refs: dict[str, int] = {}
        for index, bank in enumerate(bank_records):
            for ref in {bank.provider_reference, bank.bank_transaction_id}:
                if ref:
                    bank_refs.setdefault(ref, index)

        for local in local_records:
            exact_bank_index = None
            for ref in {local.provider_reference, *(_reference_values(local.metadata.get("provider_response") or {}))}:
                if ref and ref in bank_refs and bank_refs[ref] not in used_bank_indexes:
                    exact_bank_index = bank_refs[ref]
                    break
            if exact_bank_index is not None:
                used_bank_indexes.add(exact_bank_index)
                results.append(_merge_records(local, bank_records[exact_bank_index], confidence="exact"))
                continue

            strong_bank_index = None
            for index, bank in enumerate(bank_records):
                if index in used_bank_indexes:
                    continue
                if _strong_match(local, bank):
                    strong_bank_index = index
                    break
            if strong_bank_index is not None:
                used_bank_indexes.add(strong_bank_index)
                results.append(_merge_records(local, bank_records[strong_bank_index], confidence="strong"))
                continue

            results.append(local)

        for index, bank in enumerate(bank_records):
            if index not in used_bank_indexes:
                results.append(bank)

        return sorted(results, key=lambda record: (record.effective_at, record.provider_reference or ""), reverse=True)


def default_support_window(today: date | None = None) -> tuple[date, date]:
    """Return the default recent support lookup window."""
    end = today or datetime.now(UTC).date()
    return end - timedelta(days=90), end
