"""Advisory pre-debit risk concerns for transfer execution."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from banking.persistence.unit_of_work import UnitOfWork
from shared.config.settings import settings
from shared.database.enums import TransactionStatusEnum
from shared.money import MoneyAmount, naira_to_json, to_naira

RiskDecisionValue = Literal["allow", "warn"]


@dataclass(frozen=True, slots=True)
class RiskDecisionResult:
    """Advisory risk decision returned before a transfer can debit user accounts."""

    decision: RiskDecisionValue
    reason_codes: list[str] = field(default_factory=list)
    score: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision in {"allow", "warn"}

    @property
    def has_concerns(self) -> bool:
        return bool(self.reason_codes)


class RiskDecisionService:
    """Evaluates advisory launch-time risk rules before any provider debit."""

    async def evaluate_transfer(
        self,
        *,
        uow: UnitOfWork,
        payload: Any,
        context: Any,
        worker_context: Any,
    ) -> RiskDecisionResult:
        if not settings.transfer_risk_enabled:
            return RiskDecisionResult(decision="allow", metadata={"risk_disabled": True})

        user_id = str(getattr(worker_context, "user_id", "") or "")
        idempotency_key = str(getattr(payload, "idempotency_key", "") or "")
        amount = to_naira(getattr(payload, "amount", None)) or Decimal("0.00")
        if not user_id or not idempotency_key or amount <= 0:
            return RiskDecisionResult(decision="allow", metadata={"risk_skipped": "missing_context"})

        now = datetime.now(UTC).replace(tzinfo=None)
        reason_codes: list[str] = []
        metadata: dict[str, Any] = {
            "amount": naira_to_json(amount),
            "idempotency_key": idempotency_key,
            "channel": getattr(context, "channel", None),
            "channel_identity_present": bool(getattr(context, "channel_identity", None)),
        }

        if amount >= settings.manual_review_amount_ngn:
            metadata["high_value_threshold"] = naira_to_json(settings.manual_review_amount_ngn)
            reason_codes.append("high_value_amount")

        if await self._is_new_or_risky_beneficiary(uow, payload, user_id, amount, now, metadata):
            reason_codes.append("new_or_unsaved_beneficiary")

        if await self._is_new_channel_identity(uow, context, now, metadata):
            reason_codes.append("new_channel_identity")

        velocity_reasons = await self._velocity_reasons(uow, user_id, idempotency_key, amount, now, metadata)
        reason_codes.extend(velocity_reasons)

        if self._is_first_high_value_pooled_transfer(payload, amount):
            prior_completed = False
            if uow.funded_transfers:
                prior_completed = await uow.funded_transfers.has_prior_completed_pooled_transfer(
                    user_id,
                    exclude_idempotency_key=idempotency_key,
                )
            if not prior_completed:
                reason_codes.append("first_high_value_pooled_transfer")

        reason_codes = sorted(set(reason_codes))
        decision: RiskDecisionValue = "warn" if reason_codes else "allow"
        score = min(100, len(reason_codes) * 25)
        result = RiskDecisionResult(decision=decision, reason_codes=reason_codes, score=score, metadata=metadata)

        if uow.risk_decisions:
            await uow.risk_decisions.record(
                user_id=user_id,
                idempotency_key=idempotency_key,
                decision=decision,
                score=score,
                reason_codes=reason_codes,
                metadata=metadata,
            )

        return result

    async def _is_new_or_risky_beneficiary(
        self,
        uow: UnitOfWork,
        payload: Any,
        user_id: str,
        amount: MoneyAmount,
        now: datetime,
        metadata: dict[str, Any],
    ) -> bool:
        if getattr(payload, "is_self", False):
            return False

        has_saved_binding = bool(
            getattr(payload, "beneficiary_id", None)
            or getattr(payload, "resolved_from_saved_beneficiary", False)
        )
        if not has_saved_binding and amount >= settings.new_beneficiary_limit_ngn:
            metadata["beneficiary_state"] = "unsaved"
            return True

        if not uow.beneficiaries or not getattr(payload, "recipient_account", None):
            return False

        beneficiary = await uow.beneficiaries.get_transfer_by_account(
            user_id,
            str(payload.recipient_account),
            getattr(payload, "recipient_bank_code", None),
        )
        if not beneficiary:
            return False

        created_at = getattr(beneficiary, "created_at", None)
        if not created_at:
            return False
        age_seconds = (now - created_at).total_seconds()
        metadata["beneficiary_age_seconds"] = age_seconds
        return (
            age_seconds < int(settings.new_beneficiary_cooling_seconds)
            and amount >= settings.new_beneficiary_limit_ngn
        )

    async def _is_new_channel_identity(
        self,
        uow: UnitOfWork,
        context: Any,
        now: datetime,
        metadata: dict[str, Any],
    ) -> bool:
        channel = str(getattr(context, "channel", "") or "")
        channel_identity = str(getattr(context, "channel_identity", "") or "")
        if not channel or not channel_identity or not uow.users:
            return False
        identity = await uow.users.get_channel_identity_record(channel, channel_identity)
        if not identity or not getattr(identity, "created_at", None):
            return False
        age_seconds = (now - identity.created_at).total_seconds()
        metadata["channel_identity_age_seconds"] = age_seconds
        return age_seconds < int(settings.new_channel_cooling_seconds)

    async def _velocity_reasons(
        self,
        uow: UnitOfWork,
        user_id: str,
        idempotency_key: str,
        amount: MoneyAmount,
        now: datetime,
        metadata: dict[str, Any],
    ) -> list[str]:
        if not uow.transactions:
            return []
        tracked_statuses = [
            TransactionStatusEnum.PENDING.value,
            TransactionStatusEnum.PROCESSING.value,
            TransactionStatusEnum.SUCCESSFUL.value,
        ]
        one_hour = now - timedelta(hours=1)
        one_day = now - timedelta(days=1)
        hourly = [
            tx
            for tx in await uow.transactions.get_transfers_since(user_id, one_hour, statuses=tracked_statuses)
            if str(getattr(tx, "idempotency_key", "")) != idempotency_key
        ]
        daily = [
            tx
            for tx in await uow.transactions.get_transfers_since(user_id, one_day, statuses=tracked_statuses)
            if str(getattr(tx, "idempotency_key", "")) != idempotency_key
        ]
        hourly_amount = amount + sum(
            (to_naira(getattr(tx, "amount", None)) or Decimal("0.00")) for tx in hourly
        )
        daily_amount = amount + sum((to_naira(getattr(tx, "amount", None)) or Decimal("0.00")) for tx in daily)
        metadata.update(
            {
                "hourly_transfer_count": len(hourly) + 1,
                "hourly_transfer_amount": naira_to_json(hourly_amount),
                "daily_transfer_amount": naira_to_json(daily_amount),
            }
        )

        reasons: list[str] = []
        if len(hourly) + 1 > int(settings.transfer_hourly_count_limit):
            reasons.append("hourly_count_velocity")
        if hourly_amount > settings.transfer_hourly_amount_limit_ngn:
            reasons.append("hourly_amount_velocity")
        if daily_amount > settings.transfer_daily_amount_limit_ngn:
            reasons.append("daily_amount_velocity")
        return reasons

    @staticmethod
    def _is_first_high_value_pooled_transfer(payload: Any, amount: MoneyAmount) -> bool:
        funding_plan = getattr(payload, "funding_plan", None) or {}
        is_multi_source = bool(funding_plan and not funding_plan.get("is_single_source", True))
        return is_multi_source and amount >= settings.first_pooled_transfer_limit_ngn
