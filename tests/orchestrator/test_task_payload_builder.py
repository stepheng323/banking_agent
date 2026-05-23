from apps.chat.src.agent.orchestrator.utils.task_payload import (
    _derive_recipient_from_user_text,
    _derive_recipients_from_user_text,
    _derive_transfer_schedule_fields,
    build_task_spec_from_plan_item,
)
from shared.types.planner import PlannedTask, TaskParameters


def test_transfer_recipient_not_in_user_text_is_dropped() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to tolu",
        parameters=TaskParameters(amount=5000, recipient="Tolu Adebayo"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to tolu",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "transfer"
    assert spec.payload.get("recipient_name") == "tolu"
    assert spec.payload.get("skip_extraction") is True


def test_transfer_recipient_in_user_text_is_preserved() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to Tolu Adebayo",
        parameters=TaskParameters(amount=5000, recipient="Tolu Adebayo"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to tolu adebayo",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "Tolu Adebayo"
    assert spec.payload.get("skip_extraction") is True


def test_airtime_still_uses_skip_extraction() -> None:
    plan_item = PlannedTask(
        task_id="a1",
        action="buy_airtime",
        executor="airtime",
        instruction="Buy airtime",
        parameters=TaskParameters(amount=1000, recipient_phone="08030000000"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy airtime",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "airtime"
    assert spec.payload.get("skip_extraction") is True


def test_airtime_maps_phone_to_recipient_phone_when_only_phone_is_present() -> None:
    plan_item = PlannedTask(
        task_id="a1",
        action="buy_airtime",
        executor="airtime",
        instruction="Buy airtime for 08130000000",
        parameters=TaskParameters(amount=1000, phone="08130000000"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy airtime for 08130000000",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "airtime"
    assert spec.payload.get("recipient_phone") == "08130000000"
    assert spec.payload.get("phone") == "08130000000"


def test_data_maps_phone_and_plan_into_runtime_fields() -> None:
    plan_item = PlannedTask(
        task_id="d1",
        action="buy_data",
        executor="data",
        instruction="Buy 1gb for 08130000000 mtn",
        parameters=TaskParameters(phone="08130000000", plan="1GB", network="MTN"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy 1gb for 08130000000 mtn",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "data"
    assert spec.payload.get("target_phone") == "08130000000"
    assert spec.payload.get("plan_name") == "1GB"
    assert spec.payload.get("network") == "MTN"


def test_data_maps_budget_to_amount_when_amount_missing() -> None:
    plan_item = PlannedTask(
        task_id="d1",
        action="buy_data",
        executor="data",
        instruction="Buy data 2k for my line",
        parameters=TaskParameters(budget="2k"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy data 2k for my line",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "data"
    assert spec.payload.get("amount") == 2000


def test_transfer_maps_planner_bank_name_and_normalizes_recipient_account() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Please send 5k to 816 251 1023 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="816 251 1023 Access",
            recipient_account="816 251 1023",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Please send 5k to 816 251 1023 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "transfer"
    assert spec.payload.get("amount") == 5000
    assert spec.payload.get("recipient_account") == "8162511023"
    assert spec.payload.get("recipient_bank_name") == "Access Bank"


def test_transfer_drops_ungrounded_planner_destination_account_and_bank() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to Mum",
        parameters=TaskParameters(
            amount=5000,
            recipient="Mum",
            recipient_account="8162511023",
            bank_name="Zenith Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to mum",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "Mum"
    assert "recipient_account" not in spec.payload
    assert "recipient_bank_name" not in spec.payload


def test_transfer_keeps_grounded_bank_and_drops_ungrounded_account() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to Mum via Zenith Bank",
        parameters=TaskParameters(
            amount=5000,
            recipient="Mum",
            recipient_account="8162511023",
            bank_name="Zenith Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to mum via zenith bank",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "Mum"
    assert "recipient_account" not in spec.payload
    assert spec.payload.get("recipient_bank_name") == "Zenith Bank"


def test_transfer_keeps_10_digit_account_starting_with_zero() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to 0760505261 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="0760505261 Access",
            recipient_account="0760505261",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 5k to 0760505261 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_account") == "0760505261"


def test_transfer_trims_leading_zero_for_11_digit_account() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to 08162511023 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="08162511023 Access",
            recipient_account="08162511023",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 5k to 08162511023 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_account") == "8162511023"


def test_transfer_keeps_11_digit_account_without_leading_zero() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 5k to 18162511023 Access.",
        parameters=TaskParameters(
            amount=5000,
            recipient="18162511023 Access",
            recipient_account="18162511023",
            bank_name="Access Bank",
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 5k to 18162511023 Access.",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_account") == "18162511023"


def test_transfer_command_verb_is_not_used_as_recipient_name() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="I want to send 8k",
        parameters=TaskParameters(amount=8000, recipient="send"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "I want to send 8k",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert not spec.payload.get("recipient_name")


def test_transfer_recipient_derivation_prefers_preposition_target() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="send 5k to tolu",
        parameters=TaskParameters(amount=5000, recipient="send to tolu"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 5k to tolu",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "tolu"


def test_transfer_recipient_list_derivation_supports_and_separator() -> None:
    recipients = _derive_recipients_from_user_text("send 10k to mum and tolu")
    assert recipients == ["mum", "tolu"]


def test_transfer_recipient_list_derivation_supports_comma_and_and_separator() -> None:
    recipients = _derive_recipients_from_user_text("send 10k each to mum, tolu and doyin")
    assert recipients == ["mum", "tolu", "doyin"]


def test_transfer_recipient_list_derivation_supports_between_separator() -> None:
    recipients = _derive_recipients_from_user_text("split 20k 70/30 btw mum and gaines")
    assert recipients == ["mum", "gaines"]


def test_transfer_single_recipient_derivation_uses_first_candidate_from_list() -> None:
    recipient = _derive_recipient_from_user_text("Mum and Tolu", "send 10k to mum and tolu")
    assert recipient == "mum"


def test_transfer_single_recipient_derivation_prefers_best_matching_candidate() -> None:
    recipient = _derive_recipient_from_user_text("Tolu Adebayo", "split 10k btw mum and tolu")
    assert recipient == "tolu"


def test_transfer_single_recipient_derivation_does_not_guess_first_candidate_in_multi_recipient_text() -> None:
    recipient = _derive_recipient_from_user_text("David Johnson", "split 10k btw mum and tolu")
    assert recipient is None


def test_transfer_reference_is_mapped_to_recipient_reference() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 10k to her",
        parameters=TaskParameters(amount=10000, recipient="her", reference={"selector": "previous"}),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 10k to her",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_reference") == {"selector": "previous"}


def test_transfer_payload_builder_preserves_authoritative_fanout_recipient_binding() -> None:
    plan_item = PlannedTask(
        task_id="t1_r2",
        action="send_money",
        executor="transfer",
        instruction="Split 10k btw mum and tolu",
        parameters=TaskParameters(
            amount=5000,
            recipient="Tolu Adebayo",
            recipient_name="Tolu Adebayo",
            recipient_binding_source="fanout",
            recipient_binding_index=2,
        ),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "split 10k btw mum and tolu",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "tolu"
    assert spec.payload.get("recipient_binding_source") == "fanout"


def test_transfer_payload_builder_keeps_percentage_and_transfer_all_fields() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send half my zenith balance to Mum",
        parameters=TaskParameters(
            recipient="Mum",
            source_bank_name="Zenith Bank",
            transfer_percentage=50,
            transfer_all=False,
        ),
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send half my zenith balance to Mum",
        preserve_existing_action_instruction=False,
        include_skip_extraction=False,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("recipient_name") == "Mum"
    assert spec.payload.get("source_bank_name") == "Zenith Bank"
    assert spec.payload.get("transfer_percentage") == 50
    assert spec.payload.get("transfer_all") is False


def test_transfer_payload_builder_coerces_symbolic_all_amount_to_transfer_all() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send everything in my First Bank to Mum",
        parameters=TaskParameters(
            amount="all",
            recipient="Mum",
            source_bank_name="First Bank",
        ),
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send everything in my First Bank to Mum",
        preserve_existing_action_instruction=False,
        include_skip_extraction=False,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("amount") is None
    assert spec.payload.get("transfer_all") is True
    assert spec.payload.get("transfer_percentage") is None


def test_transfer_payload_builder_coerces_symbolic_half_amount_to_percentage() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send half my Zenith balance to Mum",
        parameters=TaskParameters(
            amount="half",
            recipient="Mum",
            source_bank_name="Zenith Bank",
        ),
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send half my Zenith balance to Mum",
        preserve_existing_action_instruction=False,
        include_skip_extraction=False,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("amount") is None
    assert spec.payload.get("transfer_percentage") == 50
    assert spec.payload.get("transfer_all") is False


def test_transfer_possessive_command_verb_is_not_used_as_recipient_name() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="I want to send money",
        parameters=TaskParameters(amount=8000, recipient="send's"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "I want to send money",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert not spec.payload.get("recipient_name")


def test_schedule_fields_parse_weekly_without_default_time() -> None:
    fields = _derive_transfer_schedule_fields(
        "send 10k to mum every friday",
        schedule_text=None,
        scheduled_text=None,
        recurring_flag=True,
    )
    assert fields["recurrence_type"] == "weekly"
    assert fields["schedule_day_of_week"] == 4
    assert "schedule_time_local" not in fields
    assert fields["schedule_timezone"] == "Africa/Lagos"


def test_schedule_fields_parse_one_time_with_explicit_time() -> None:
    fields = _derive_transfer_schedule_fields(
        "send 10k to mum tomorrow 8pm",
        schedule_text=None,
        scheduled_text=None,
        recurring_flag=False,
    )
    assert fields["recurrence_type"] == "one_time"
    assert fields["schedule_time_local"] == "20:00"
    assert "schedule_start_date" in fields


def test_schedule_fields_parse_common_tomorrow_typo() -> None:
    fields = _derive_transfer_schedule_fields(
        "send 10k to mum by tommorow",
        schedule_text=None,
        scheduled_text=None,
        recurring_flag=False,
    )
    assert fields["recurrence_type"] == "one_time"
    assert "schedule_start_date" in fields
    assert "schedule_time_local" not in fields


def test_transfer_send_money_infers_schedule_action_from_text() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 10k to mum tomorrow 9am",
        parameters=TaskParameters(amount=10000, recipient="mum"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "send 10k to mum tomorrow 9am",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("action") == "schedule_transfer"
    assert spec.payload.get("schedule_time_local") == "09:00"
    assert spec.payload.get("schedule_timezone") == "Africa/Lagos"


def test_transfer_send_money_infers_schedule_action_and_cleans_recipient_typo() -> None:
    plan_item = PlannedTask(
        task_id="t1",
        action="send_money",
        executor="transfer",
        instruction="Send 20k to mum by tommorow",
        parameters=TaskParameters(amount=20000, recipient="Mum By Tommorow"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "Send 20k to mum by tommorow",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("action") == "schedule_transfer"
    assert spec.payload.get("recipient_name") == "Mum"
    assert spec.payload.get("schedule_timezone") == "Africa/Lagos"
    assert "schedule_start_date" in spec.payload
    assert "schedule_time_local" not in spec.payload


def test_airtime_buy_infers_schedule_action_from_text_without_default_time() -> None:
    plan_item = PlannedTask(
        task_id="a1",
        action="buy_airtime",
        executor="airtime",
        instruction="Buy 2k airtime tomorrow",
        parameters=TaskParameters(amount=2000),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy 2k airtime tomorrow",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("action") == "schedule_airtime"
    assert spec.payload.get("schedule_timezone") == "Africa/Lagos"
    assert "schedule_start_date" in spec.payload
    assert "schedule_time_local" not in spec.payload


def test_airtime_buy_infers_monthly_schedule_with_weekday_and_time() -> None:
    plan_item = PlannedTask(
        task_id="a1",
        action="buy_airtime",
        executor="airtime",
        instruction="Buy 2k airtime every month sunday 3pm",
        parameters=TaskParameters(amount=2000),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy 2k airtime every month sunday 3pm",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("action") == "recurring_airtime"
    assert spec.payload.get("recurrence_type") == "monthly"
    assert spec.payload.get("schedule_start_date")
    assert spec.payload.get("schedule_time_local") == "15:00"


def test_data_buy_infers_recurring_schedule_action_from_text() -> None:
    plan_item = PlannedTask(
        task_id="d1",
        action="buy_data",
        executor="data",
        instruction="Buy 1GB data every Friday 8am",
        parameters=TaskParameters(plan="1GB"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy 1GB data every Friday 8am",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("action") == "recurring_data"
    assert spec.payload.get("recurrence_type") == "weekly"
    assert spec.payload.get("schedule_time_local") == "08:00"


def test_data_buy_infers_monthly_schedule_with_weekday_and_time() -> None:
    plan_item = PlannedTask(
        task_id="d1",
        action="buy_data",
        executor="data",
        instruction="Buy 1GB data every month sunday 3pm",
        parameters=TaskParameters(plan="1GB"),
        risk="MONEY_MOVE",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "buy 1GB data every month sunday 3pm",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("action") == "recurring_data"
    assert spec.payload.get("recurrence_type") == "monthly"
    assert spec.payload.get("schedule_start_date")
    assert spec.payload.get("schedule_time_local") == "15:00"


def test_schedule_management_action_uses_schedule_executor_even_when_planner_returns_transfer() -> None:
    plan_item = PlannedTask(
        task_id="s1",
        action="list_scheduled_transactions",
        executor="transfer",
        instruction="How many scheduled transaction is pending",
        parameters=TaskParameters(schedule_response_mode="count"),
        risk="READ_ONLY",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "How many scheduled transaction is pending",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.type == "schedule"
    assert spec.payload.get("action") == "list_scheduled_transactions"
    assert spec.payload.get("schedule_response_mode") == "count"
    assert "skip_extraction" not in spec.payload


def test_query_payload_prefers_user_message_over_planner_instruction() -> None:
    plan_item = PlannedTask(
        task_id="q1",
        action="transaction_search",
        executor="query",
        instruction="Check today's spending",
        parameters=TaskParameters(),
        risk="READ_ONLY",
    )

    spec = build_task_spec_from_plan_item(
        plan_item,
        "How much did I spend today",
        preserve_existing_action_instruction=True,
        include_skip_extraction=True,
        strip_transfer_recipient_suffix=True,
        format_narration_requires_recipient_field=False,
    )

    assert spec.payload.get("message") == "How much did I spend today"
