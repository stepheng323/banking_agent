from shared.services.task_planner_normalizer import normalize_planner_transaction_output
from shared.types.planner import PlannedTask, PlannerOutput, TaskParameters


def _planner_output(tasks: list[PlannedTask]) -> PlannerOutput:
    return PlannerOutput(
        primary_intent="mixed" if len(tasks) > 1 else tasks[0].executor,
        tasks=tasks,
        confidence=0.9,
        detected_language="English",
    )


def test_transfer_one_shot_normalizer_patches_missing_account_bank_and_amount() -> None:
    planner_output = _planner_output(
        [
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to 0760505261 First Bank",
                parameters=TaskParameters(recipient_name="Mum"),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Send 20k to 0760505261 First Bank")
    params = normalized.tasks[0].parameters
    assert params.amount == 20000
    assert params.recipient_account == "0760505261"
    assert params.bank_name == "First Bank"


def test_transfer_normalizer_does_not_overwrite_existing_fields() -> None:
    planner_output = _planner_output(
        [
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to 0760505261 First Bank",
                parameters=TaskParameters(
                    amount=18000,
                    recipient_account="8162511023",
                    bank_name="Opay",
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Send 20k to 0760505261 First Bank")
    params = normalized.tasks[0].parameters
    assert params.amount == 18000
    assert params.recipient_account == "8162511023"
    assert params.bank_name == "Opay"


def test_transfer_normalizer_skips_ambiguous_account_candidates() -> None:
    planner_output = _planner_output(
        [
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="send to 0760505261 first bank and 08130000000 gtbank",
                parameters=TaskParameters(),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(
        planner_output,
        "send to 0760505261 first bank and 08130000000 gtbank",
    )
    params = normalized.tasks[0].parameters
    assert params.recipient_account is None
    assert params.bank_name is None


def test_airtime_one_shot_normalizer_patches_amount_phone_and_network() -> None:
    planner_output = _planner_output(
        [
            PlannedTask(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="Abeg buy 2k airtime for 08031234567 mtn",
                parameters=TaskParameters(),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Abeg buy 2k airtime for 08031234567 mtn")
    params = normalized.tasks[0].parameters
    assert params.amount == 2000
    assert params.recipient_phone == "08031234567"
    assert params.phone == "08031234567"
    assert params.network == "MTN"


def test_data_one_shot_normalizer_patches_phone_network_plan_and_amount_from_budget() -> None:
    planner_output = _planner_output(
        [
            PlannedTask(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Jowo ra data 1gb fun 08031234567 mtn",
                parameters=TaskParameters(budget="2k"),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Jowo ra data 1gb fun 08031234567 mtn")
    params = normalized.tasks[0].parameters
    assert params.amount == 2000
    assert params.recipient_phone == "08031234567"
    assert params.phone == "08031234567"
    assert params.network == "MTN"
    assert params.plan == "1GB"


def test_mixed_turn_normalizer_patches_each_transaction_task_using_task_instruction() -> None:
    planner_output = _planner_output(
        [
            PlannedTask(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to 0760505261 First Bank",
                parameters=TaskParameters(recipient_name="Mum"),
                risk="MONEY_MOVE",
            ),
            PlannedTask(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="buy 2k airtime for 08031234567 mtn",
                parameters=TaskParameters(),
                risk="MONEY_MOVE",
            ),
        ]
    )

    normalized = normalize_planner_transaction_output(
        planner_output,
        "Send 10k to 0760505261 First Bank and buy 2k airtime for 08031234567 mtn",
    )
    transfer_params = normalized.tasks[0].parameters
    airtime_params = normalized.tasks[1].parameters

    assert transfer_params.recipient_account == "0760505261"
    assert transfer_params.bank_name == "First Bank"
    assert transfer_params.amount == 10000
    assert airtime_params.recipient_phone == "08031234567"
    assert airtime_params.network == "MTN"
    assert airtime_params.amount == 2000
