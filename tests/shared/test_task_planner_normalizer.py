from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer import (
    normalize_planner_transaction_output,
    normalize_planner_transaction_output_with_quality,
)
from shared.types.planner import (
    AirtimeTaskParameters,
    DataTaskParameters,
    PlannedTask,
    PlannerOutput,
    TransferTaskParameters,
    make_planned_task,
)


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
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to 0760505261 First Bank",
                parameters=TransferTaskParameters(recipient_name="Mum"),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Send 20k to 0760505261 First Bank")
    params = normalized.tasks[0].parameters
    assert params.amount == 20000
    assert params.recipient_account == "0760505261"
    assert params.bank_name == "First Bank"


def test_transfer_normalizer_preserves_typed_self_destination() -> None:
    """The planner signal survives normalization without raw-text inference."""
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t_self",
                action="send_money",
                executor="transfer",
                instruction="Send 5k to recipient",
                parameters=TransferTaskParameters(
                    amount=5000,
                    recipient_name="recipient",
                    bank_name="Access Bank",
                    is_self=True,
                ),
                risk="MONEY_MOVE",
                source_clause_index=2,
            )
        ]
    )

    normalized = normalize_planner_transaction_output(
        planner_output,
        "unrelated original wording",
    )

    assert normalized.tasks[0].parameters.is_self is True
    assert normalized.tasks[0].parameters.recipient is None
    assert normalized.tasks[0].parameters.recipient_name is None


def test_transfer_normalizer_does_not_overwrite_existing_fields() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to 0760505261 First Bank",
                parameters=TransferTaskParameters(
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


def test_transfer_normalizer_extracts_simple_instruction_amount_when_planner_amount_is_zero() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 20k to mum",
                parameters=TransferTaskParameters(amount=0, recipient_name="mum"),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Send 20k to mum")
    params = normalized.tasks[0].parameters
    assert params.amount == 20000
    assert params.recipient_name == "mum"


def test_transfer_normalizer_skips_ambiguous_account_candidates() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="send to 0760505261 first bank and 08130000000 gtbank",
                parameters=TransferTaskParameters(),
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


def test_transfer_normalizer_repairs_missing_percentage_for_account_aware_balance_share() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send half of the Zenith Bank balance to Mum",
                parameters=TransferTaskParameters(
                    recipient_name="Mum",
                    source_bank_name="Zenith Bank",
                    source_account_index=0,
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "send half my zenith to mum")
    params = normalized.tasks[0].parameters
    assert params.recipient_name == "Mum"
    assert params.source_bank_name == "Zenith Bank"
    assert params.transfer_percentage == 50
    assert params.amount is None
    assert params.transfer_all is False


def test_transfer_normalizer_repairs_missing_transfer_all_for_account_aware_balance_share() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send everything in my First Bank to Mum",
                parameters=TransferTaskParameters(
                    recipient_name="Mum",
                    source_bank_name="First Bank",
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "send everything in my first bank to mum")
    params = normalized.tasks[0].parameters
    assert params.recipient_name == "Mum"
    assert params.source_bank_name == "First Bank"
    assert params.transfer_all is True
    assert params.transfer_percentage is None
    assert params.amount is None


def test_transfer_normalizer_coerces_symbolic_all_amount_to_transfer_all() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send everything in my First Bank to Mum",
                parameters=TransferTaskParameters(
                    amount="all",
                    recipient_name="Mum",
                    source_bank_name="First Bank",
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "send everything in my first bank to mum")
    params = normalized.tasks[0].parameters
    assert params.recipient_name == "Mum"
    assert params.source_bank_name == "First Bank"
    assert params.transfer_all is True
    assert params.transfer_percentage is None
    assert params.amount is None


def test_transfer_normalizer_coerces_symbolic_balance_share_amount_to_percentage() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send half of the Zenith Bank balance to Mum",
                parameters=TransferTaskParameters(
                    amount="half",
                    recipient_name="Mum",
                    source_bank_name="Zenith Bank",
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "send half my zenith balance to mum")
    params = normalized.tasks[0].parameters
    assert params.recipient_name == "Mum"
    assert params.source_bank_name == "Zenith Bank"
    assert params.transfer_percentage == 50
    assert params.transfer_all is False
    assert params.amount is None


def test_transfer_normalizer_drops_malformed_explicit_split_when_recipient_allocations_exist() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Split 20k 70/30 between Mum and Gaines",
                parameters=TransferTaskParameters(
                    amount=20000,
                    recipient_allocations=[
                        {"recipient_name": "Mum", "amount": 14000},
                        {"recipient_name": "Gaines", "amount": 6000},
                    ],
                    explicit_split={
                        "recipient_allocations':[{": 14000.0,
                        "recipient_name": 0.0,
                        "amount": 6000.0,
                    },
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "split 20k 70/30 btw mum and gaines")
    params = normalized.tasks[0].parameters
    assert params.recipient_allocations is not None
    assert [item.recipient_name for item in params.recipient_allocations] == ["Mum", "Gaines"]
    assert [item.amount for item in params.recipient_allocations] == [14000, 6000]
    assert params.explicit_split is None


def test_transfer_normalizer_keeps_valid_source_explicit_split_with_recipient_allocations() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Split 20k between Mum and Gaines from Access and GTB",
                parameters=TransferTaskParameters(
                    amount=20000,
                    recipient_allocations=[
                        {"recipient_name": "Mum", "amount": 14000},
                        {"recipient_name": "Gaines", "amount": 6000},
                    ],
                    explicit_split={"Access": 10000.0, "GTB": 10000.0},
                ),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(
        planner_output,
        "split 20k 70/30 btw mum and gaines from access and gtb",
    )
    params = normalized.tasks[0].parameters
    assert params.explicit_split == {"Access": 10000.0, "GTB": 10000.0}


def test_transfer_normalizer_rewrites_mixed_primary_intent_for_transfer_only_batch() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TransferTaskParameters(amount=10000, recipient_name="Mum"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t2",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Tolu",
                parameters=TransferTaskParameters(amount=10000, recipient_name="Tolu"),
                risk="MONEY_MOVE",
            ),
        ],
        confidence=0.9,
        detected_language="English",
    )

    normalized = normalize_planner_transaction_output(planner_output, "send 10k each to mum and tolu")

    assert normalized.primary_intent == "transfer"


def test_transfer_normalizer_keeps_mixed_primary_intent_when_non_transfer_task_remains() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to Mum",
                parameters=TransferTaskParameters(amount=10000, recipient_name="Mum"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy 2k airtime",
                parameters=AirtimeTaskParameters(amount=2000, is_self=True),
                risk="MONEY_MOVE",
            ),
        ],
        confidence=0.9,
        detected_language="English",
    )

    normalized = normalize_planner_transaction_output(planner_output, "send 10k to mum and buy 2k airtime")

    assert normalized.primary_intent == "mixed"


def test_airtime_one_shot_normalizer_patches_amount_phone_and_network() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="Abeg buy 2k airtime for 08031234567 mtn",
                parameters=AirtimeTaskParameters(),
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


def test_inferred_airtime_network_from_planner_phone_keeps_planner_quality_clean() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="Buy 1k airtime for me",
                parameters=AirtimeTaskParameters(amount=1000, recipient_phone="08162511023", is_self=True),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(planner_output, "Buy 1k airtime for me")

    assert normalized.tasks[0].parameters.network == "MTN"
    assert quality.clean


def test_data_one_shot_normalizer_patches_phone_network_plan_and_amount_from_budget() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Jowo ra data 1gb fun 08031234567 mtn",
                parameters=DataTaskParameters(budget="2k"),
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


def test_data_normalizer_repairs_planner_amount_data_size_to_plan() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Buy 1GB MTN data for me",
                parameters=DataTaskParameters(amount="1GB", network="MTN"),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Buy 1GB MTN data for me")
    params = normalized.tasks[0].parameters
    assert params.amount is None
    assert params.plan == "1GB"
    assert params.network == "MTN"


def test_data_normalizer_does_not_extract_data_size_as_amount() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Buy 1GB MTN data for me",
                parameters=DataTaskParameters(),
                risk="MONEY_MOVE",
            )
        ]
    )

    normalized = normalize_planner_transaction_output(planner_output, "Buy 1GB MTN data for me")
    params = normalized.tasks[0].parameters
    assert params.amount is None
    assert params.plan == "1GB"
    assert params.network == "MTN"


def test_mixed_turn_normalizer_patches_each_transaction_task_using_task_instruction() -> None:
    planner_output = _planner_output(
        [
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to 0760505261 First Bank",
                parameters=TransferTaskParameters(recipient_name="Mum"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="a1",
                action="buy_airtime",
                executor="airtime",
                instruction="buy 2k airtime for 08031234567 mtn",
                parameters=AirtimeTaskParameters(),
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
