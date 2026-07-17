from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from banking.runtime.results import TransactionOutcome
from banking.scheduling.services.recurrence import (
    SCHEDULE_TIMEZONE,
    compute_initial_next_run_utc,
    format_lagos_schedule_datetime,
    today_lagos,
)
from banking.transactions.shared.schedule_management import (
    build_schedule_context_items,
    format_schedule_context_blocks,
)
from banking.transfers.models.types import TransferGates, TransferPayload
from banking.transfers.pipeline_factory import build_transfer_pipeline
from banking.transfers.scheduling import TransferSchedulingHandler
from shared.messaging.body_blocks import render_body_blocks_text
from shared.types.conversation_sets import BulkMutationRequest, EntitySelectionRef, ScheduleQueryContract
from shared.types.read import ReadRequest, ResponseShape


class _FakeScheduleRepo:
    def __init__(self, schedules: list[SimpleNamespace]) -> None:
        self.schedules = schedules

    async def get_active_by_user(self, user_id: str, limit: int = 20) -> list[SimpleNamespace]:
        del user_id, limit
        return [schedule for schedule in self.schedules if schedule.status == "active"]

    async def get_filtered_by_user(
        self,
        user_id: str,
        *,
        statuses: list[str],
        domains: list[str],
        recurrence: str | None,
        recipient_name: str | None,
        starts_at: datetime | None,
        ends_at: datetime | None,
        selected_ids: list[str],
        limit: int,
        offset: int,
    ) -> list[SimpleNamespace]:
        del user_id, recurrence, recipient_name, starts_at, ends_at
        matches = [
            schedule
            for schedule in self.schedules
            if schedule.status in statuses
            and (not domains or schedule.domain in domains)
            and (not selected_ids or str(schedule.id) in selected_ids)
        ]
        return matches[offset : offset + limit]

    async def count_filtered_by_user(
        self,
        user_id: str,
        *,
        statuses: list[str],
        domains: list[str],
        recurrence: str | None,
        recipient_name: str | None,
        starts_at: datetime | None,
        ends_at: datetime | None,
        selected_ids: list[str],
    ) -> int:
        rows = await self.get_filtered_by_user(
            user_id,
            statuses=statuses,
            domains=domains,
            recurrence=recurrence,
            recipient_name=recipient_name,
            starts_at=starts_at,
            ends_at=ends_at,
            selected_ids=selected_ids,
            limit=len(self.schedules) + 1,
            offset=0,
        )
        return len(rows)

    async def get_active_for_user_for_update(self, schedule_id: str, user_id: str) -> SimpleNamespace | None:
        del user_id
        return next(
            (
                schedule
                for schedule in self.schedules
                if str(schedule.id) == schedule_id and schedule.status == "active"
            ),
            None,
        )

    async def get_active_ids_for_user_for_update(
        self,
        schedule_ids: list[str],
        user_id: str,
    ) -> list[SimpleNamespace]:
        del user_id
        return [
            schedule
            for schedule in self.schedules
            if str(schedule.id) in schedule_ids and schedule.status == "active"
        ]


class _FakeUnitOfWork:
    def __init__(self, repo: _FakeScheduleRepo) -> None:
        self.scheduled_instructions = repo
        self.committed = False

    async def __aenter__(self) -> "_FakeUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        del exc_type, exc_val, exc_tb
        return False

    async def commit(self) -> None:
        self.committed = True


def _scheduling() -> TransferSchedulingHandler:
    return TransferSchedulingHandler(build_pipeline=build_transfer_pipeline)


def _list_payload(*, response_shape: ResponseShape = "surface_list") -> TransferPayload:
    operation = "count" if response_shape == "fact_count" else "list"
    return TransferPayload(
        read_request=ReadRequest(subject="schedule", response_shape=response_shape),
        schedule_contract=ScheduleQueryContract(operation=operation, response_shape=response_shape),
    )


class _StubRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls.append((key, ttl, value))


def _future_schedule_date() -> date:
    return today_lagos() + timedelta(days=1)


def _expected_next_run_at(local_time: str) -> datetime:
    next_run_at = compute_initial_next_run_utc(
        recurrence_type="one_time",
        start_date=_future_schedule_date().isoformat(),
        local_time=local_time,
        timezone=SCHEDULE_TIMEZONE,
    )
    assert next_run_at is not None
    return next_run_at


def _schedule(
    schedule_id: str,
    *,
    domain: str,
    payload_snapshot: dict,
    local_time: str = "08:00",
) -> SimpleNamespace:
    next_run_at = _expected_next_run_at(local_time)
    return SimpleNamespace(
        id=schedule_id,
        user_id="user-1",
        domain=domain,
        status="active",
        action=f"schedule_{domain}",
        payload_snapshot=payload_snapshot,
        timezone="Africa/Lagos",
        recurrence_type="one_time",
        start_date=_future_schedule_date().isoformat(),
        local_time=local_time,
        day_of_week=None,
        day_of_month=None,
        end_date=None,
        next_run_at_utc=next_run_at,
        cancelled_at=None,
        updated_at=datetime(2026, 7, 17, 8, 0, 0),
    )


@pytest.mark.asyncio
async def test_schedule_management_list_shows_transfer_airtime_and_data(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo(
        [
            _schedule(
                "sch-transfer",
                domain="transfer",
                payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
            ),
            _schedule(
                "sch-airtime",
                domain="airtime",
                payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
            ),
            _schedule(
                "sch-data",
                domain="data",
                payload_snapshot={"amount": 1500, "target_phone": "08162511023", "plan_name": "1GB Daily"},
            ),
        ]
    )
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _scheduling().list_schedules(data=_list_payload(), user_id="user-1", locale="en")

    assert result.outcome == TransactionOutcome.OK
    assert result.response
    assert "Scheduled transactions:" in result.response
    assert "Transfer:" in result.response
    assert "Airtime:" in result.response
    assert "Data:" in result.response
    assert "sch-transfer" not in result.response
    assert "ID:" not in result.response


def test_schedule_context_blocks_space_mixed_schedule_items_for_mobile() -> None:
    schedules = [
        _schedule(
            "sch-transfer",
            domain="transfer",
            payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
        ),
        _schedule(
            "sch-airtime",
            domain="airtime",
            payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
        ),
        _schedule(
            "sch-data",
            domain="data",
            payload_snapshot={"amount": 1500, "target_phone": "08162511023", "plan_name": "1GB Daily"},
        ),
    ]
    items = build_schedule_context_items(schedules, locale="en")

    rendered = render_body_blocks_text(format_schedule_context_blocks(items, heading="Scheduled transactions:"))

    assert rendered.startswith("Scheduled transactions")
    assert "1. Transfer — ₦5,000 to Mum\nOne Time • 8:00 AM WAT\nNext:" in rendered
    assert "\n\n2. Airtime — ₦1,000 • MTN airtime for 08162511023\nOne Time • 8:00 AM WAT" in rendered
    assert "\n\n3. Data — ₦1,500 • 1GB Daily for 08162511023\nOne Time • 8:00 AM WAT" in rendered
    assert "sch-transfer" not in rendered
    assert "ID:" not in rendered


@pytest.mark.asyncio
async def test_schedule_management_count_mode_reports_pending_count(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo(
        [
            _schedule(
                "sch-transfer",
                domain="transfer",
                payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
            ),
            _schedule(
                "sch-airtime",
                domain="airtime",
                payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
            ),
        ]
    )
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _scheduling().list_schedules(
        data=TransferPayload(
            read_request=ReadRequest(subject="schedule", response_shape="fact_count"),
            schedule_contract=ScheduleQueryContract(operation="count", response_shape="fact_count"),
        ),
        user_id="user-1",
        locale="en",
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "Pending scheduled transactions: 2."
    assert "schedule_context_items" not in result.patch
    assert result.read_result is not None
    assert result.read_result.request.response_shape == "fact_count"
    assert result.read_result.total_count == 2
    assert result.read_result.returned_count == 0


@pytest.mark.asyncio
async def test_schedule_management_count_mode_zero_uses_natural_empty_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo([])
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _scheduling().list_schedules(
        data=TransferPayload(
            read_request=ReadRequest(subject="schedule", response_shape="fact_count"),
            schedule_contract=ScheduleQueryContract(operation="count", response_shape="fact_count"),
        ),
        user_id="user-1",
        locale="en",
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You have no active scheduled transactions."
    assert " 0 " not in f" {result.response} "


@pytest.mark.asyncio
async def test_schedule_management_empty_list_uses_locale_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo([])
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _scheduling().list_schedules(data=_list_payload(), user_id="user-1", locale="pcm")

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "You no get active schedules."


@pytest.mark.asyncio
async def test_schedule_management_list_uses_locale_row_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeScheduleRepo(
        [
            _schedule(
                "sch-airtime",
                domain="airtime",
                payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
            ),
        ]
    )
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: _FakeUnitOfWork(repo))

    result = await _scheduling().list_schedules(data=_list_payload(), user_id="user-1", locale="pcm")

    assert result.outcome == TransactionOutcome.OK
    assert result.response is not None
    assert result.response.startswith("Your scheduled transactions:")
    assert "One time by 8:00 AM WAT" in result.response


@pytest.mark.asyncio
async def test_schedule_management_cancel_requires_review_before_disabling_active_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-airtime",
        domain="airtime",
        payload_snapshot={"amount": 1000, "recipient_phone": "08162511023", "network": "MTN"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)

    review = await _scheduling().cancel_schedule(
        data=TransferPayload(schedule_selector="1"),
        user_id="user-1",
        locale="en",
        user_message="delete scheduled airtime",
    )

    assert review.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert schedule.status == "active"
    assert uow.committed is False

    result = await _scheduling().cancel_schedule(
        data=TransferPayload(
            schedule_selector="1",
            bulk_mutation=review.patch["bulk_mutation"],
            confirmation={"confirmed": True},
        ),
        user_id="user-1",
        locale="en",
        user_message="delete scheduled airtime",
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response == "Cancelled 1 scheduled item(s)."
    assert schedule.status == "cancelled"
    assert schedule.cancelled_at is not None
    assert uow.committed is True


@pytest.mark.asyncio
async def test_schedule_management_material_edit_requires_pin_without_text_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)
    scheduling = _scheduling()
    payload = TransferPayload(
        schedule_selector="1",
        amount=7000,
        schedule_time_local="09:30",
    )

    auth = await scheduling.edit_schedule(
        data=payload,
        user_id="user-1",
        locale="en",
        user_message="change scheduled transfer to 7k at 9:30am",
        gates=TransferGates(),
    )

    assert auth.outcome == TransactionOutcome.NEEDS_AUTH
    assert auth.confirmation_summary
    assert "Confirm Schedule Update" not in auth.confirmation_summary
    assert "One Time at 9:30 AM WAT" in auth.confirmation_summary
    assert auth.patch["schedule_edit_requires_auth"] is True
    assert schedule.payload_snapshot["amount"] == 5000

    updated = await scheduling.edit_schedule(
        data=payload.model_copy(update=auth.patch),
        user_id="user-1",
        locale="en",
        user_message="123456",
        gates=TransferGates(pin_verified=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert updated.response is not None
    expected_next_run_at = _expected_next_run_at("09:30")
    assert "amount changed from ₦5,000 to ₦7,000" in updated.response
    assert f"Next run: {format_lagos_schedule_datetime(expected_next_run_at)}" in updated.response
    assert schedule.payload_snapshot["amount"] == 7000
    assert schedule.local_time == "09:30"
    assert schedule.next_run_at_utc == expected_next_run_at
    assert uow.committed is True


@pytest.mark.asyncio
async def test_schedule_management_material_edit_persists_schedule_pin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _StubRedis()
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)

    from shared.cache.redis_client import RedisClient

    monkeypatch.setattr(RedisClient, "get_client", classmethod(lambda cls, redis_url=None: redis))

    scheduling = _scheduling()
    payload = TransferPayload(
        idempotency_key="schedule-test-token",
        schedule_selector="1",
        amount=7000,
        schedule_time_local="09:30",
    )

    auth = await scheduling.edit_schedule(
        data=payload,
        user_id="user-1",
        locale="en",
        user_message="change scheduled transfer to 7k at 9:30am",
        gates=TransferGates(),
        phone_number="2348162511023",
        worker_context=SimpleNamespace(redis_client=None),
    )

    assert auth.outcome == TransactionOutcome.NEEDS_AUTH
    assert redis.calls == [
        ("schedule:token:schedule-test-token:phone", 3600, "2348162511023"),
    ]


@pytest.mark.asyncio
async def test_schedule_management_time_only_edit_updates_after_confirmation_without_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)
    scheduling = _scheduling()
    payload = TransferPayload(
        schedule_selector="1",
        schedule_time_local="09:30",
    )

    confirmation = await scheduling.edit_schedule(
        data=payload,
        user_id="user-1",
        locale="en",
        user_message="change scheduled transfer to 9:30am",
        gates=TransferGates(),
    )

    assert confirmation.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert confirmation.patch["schedule_edit_requires_auth"] is False
    assert schedule.local_time == "08:00"

    updated = await scheduling.edit_schedule(
        data=payload.model_copy(update=confirmation.patch),
        user_id="user-1",
        locale="en",
        user_message="yes",
        gates=TransferGates(confirmation_confirmed=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert updated.response is not None
    expected_next_run_at = _expected_next_run_at("09:30")
    assert "time changed from 8:00 AM to 9:30 AM WAT" in updated.response
    assert f"Next run: {format_lagos_schedule_datetime(expected_next_run_at)}" in updated.response
    assert schedule.payload_snapshot["amount"] == 5000
    assert schedule.local_time == "09:30"
    assert schedule.next_run_at_utc == expected_next_run_at
    assert uow.committed is True


@pytest.mark.asyncio
async def test_schedule_management_time_edit_success_uses_locale_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)
    scheduling = _scheduling()
    payload = TransferPayload(
        schedule_selector="1",
        schedule_time_local="09:30",
    )

    confirmation = await scheduling.edit_schedule(
        data=payload,
        user_id="user-1",
        locale="ha",
        user_message="change scheduled transfer to 9:30am",
        gates=TransferGates(),
    )

    assert confirmation.outcome == TransactionOutcome.NEEDS_CONFIRMATION

    updated = await scheduling.edit_schedule(
        data=payload.model_copy(update=confirmation.patch),
        user_id="user-1",
        locale="ha",
        user_message="yes",
        gates=TransferGates(confirmation_confirmed=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert updated.response is not None
    assert "Schedule an update:" in updated.response
    assert "time ya canza daga 8:00 AM zuwa 9:30 AM WAT" in updated.response


@pytest.mark.asyncio
async def test_schedule_management_narration_only_edit_does_not_require_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum", "narration": "Old note"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)
    scheduling = _scheduling()

    confirmation = await scheduling.edit_schedule(
        data=TransferPayload(schedule_selector="1", narration="New note"),
        user_id="user-1",
        locale="en",
        user_message="change the narration to new note",
        gates=TransferGates(),
    )

    assert confirmation.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert confirmation.patch["schedule_edit_requires_auth"] is False

    updated = await scheduling.edit_schedule(
        data=TransferPayload(schedule_selector="1").model_copy(update=confirmation.patch),
        user_id="user-1",
        locale="en",
        user_message="yes",
        gates=TransferGates(confirmation_confirmed=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert schedule.payload_snapshot["narration"] == "New note"


@pytest.mark.asyncio
async def test_schedule_management_recurrence_edit_requires_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = _schedule(
        "sch-transfer",
        domain="transfer",
        payload_snapshot={"amount": 5000, "recipient_name": "Mum"},
    )
    uow = _FakeUnitOfWork(_FakeScheduleRepo([schedule]))
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)
    scheduling = _scheduling()

    auth = await scheduling.edit_schedule(
        data=TransferPayload(schedule_selector="1", recurrence_type="daily", schedule_time_local="09:30"),
        user_id="user-1",
        locale="en",
        user_message="make it daily at 9:30am",
        gates=TransferGates(),
    )

    assert auth.outcome == TransactionOutcome.NEEDS_AUTH
    assert auth.patch["schedule_edit_requires_auth"] is True
    assert schedule.recurrence_type == "one_time"


@pytest.mark.asyncio
async def test_bulk_schedule_edit_reviews_once_and_applies_atomically_after_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules = [
        _schedule(
            f"sch-{index}",
            domain="transfer",
            payload_snapshot={"amount": 5000, "recipient_name": f"Person {index}"},
        )
        for index in (1, 2)
    ]
    repo = _FakeScheduleRepo(schedules)
    uow = _FakeUnitOfWork(repo)
    monkeypatch.setattr("banking.transfers.scheduling.UnitOfWork", lambda: uow)
    request = BulkMutationRequest(
        domain="schedule",
        action="edit",
        targets=[
            EntitySelectionRef(
                entity_type="schedule",
                entity_id=str(schedule.id),
                frame_id="schedule-frame",
                display_label=f"Scheduled transfer {index}",
                version_token=schedule.updated_at.isoformat(),
            )
            for index, schedule in enumerate(schedules, start=1)
        ],
        idempotency_key="bulk-schedule-edit",
    )
    scheduling = _scheduling()

    review = await scheduling.edit_schedule(
        data=TransferPayload(
            bulk_mutation=request,
            schedule_edit_patch={"amount": 6000},
        ),
        user_id="user-1",
        locale="en",
        user_message="make both 6k",
        gates=TransferGates(),
    )

    assert review.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert [schedule.payload_snapshot["amount"] for schedule in schedules] == [5000, 5000]

    updated = await scheduling.edit_schedule(
        data=TransferPayload(
            bulk_mutation=review.patch["bulk_mutation"],
            bulk_schedule_next_runs=review.patch["bulk_schedule_next_runs"],
            schedule_edit_patch=review.patch["schedule_edit_patch"],
            schedule_edit_requires_auth=True,
            confirmation={"confirmed": True},
        ),
        user_id="user-1",
        locale="en",
        user_message="yes",
        gates=TransferGates(pin_verified=True, confirmation_confirmed=True),
    )

    assert updated.outcome == TransactionOutcome.OK
    assert [schedule.payload_snapshot["amount"] for schedule in schedules] == [6000, 6000]
    assert uow.committed is True
