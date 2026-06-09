from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_normalizer import (
    normalize_planner_transaction_output_with_quality,
)
from apps.chat.src.agent.orchestrator.workflows.planner.core.task_planner_quality import PlannerQualityReport
from apps.chat.src.agent.orchestrator.workflows.planner.task_flow.task_flow_postprocessing import (
    _postprocess_planner_tasks_with_quality,
)
from shared.types.planner import (
    AirtimeTaskParameters,
    DataTaskParameters,
    PlannedTask,
    PlannerClause,
    PlannerOutput,
    RecipientAllocation,
    TransferTaskParameters,
    make_planned_task,
)


def _planner_output(task: PlannedTask) -> PlannerOutput:
    return PlannerOutput(primary_intent=task.executor, detected_language="English", tasks=[task])


def test_planner_quality_clean_when_no_normalizer_patch_needed() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Send 2k to Mum",
            parameters=TransferTaskParameters(amount=2000, recipient_name="Mum"),
            risk="MONEY_MOVE",
        )
    )

    _normalized, quality = normalize_planner_transaction_output_with_quality(planner_output, "Send 2k to Mum")

    assert quality.clean
    assert quality.dirty_reasons == ()


def test_planner_quality_clean_for_mechanical_numeric_amount_coercion() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Send 2000 to Mum",
            parameters=TransferTaskParameters(amount="2000", recipient_name="Mum"),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(planner_output, "Send 2000 to Mum")

    assert quality.clean
    assert normalized.tasks[0].parameters.amount == 2000


def test_planner_quality_dirty_when_transfer_slots_are_patched() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Send 20k to 0760505261 First Bank",
            parameters=TransferTaskParameters(recipient_name="Mum"),
            risk="MONEY_MOVE",
        )
    )

    _normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Send 20k to 0760505261 First Bank",
    )

    assert not quality.clean
    assert "normalizer.transfer.amount" in quality.dirty_reasons
    assert "normalizer.transfer.recipient_account" in quality.dirty_reasons
    assert "normalizer.transfer.bank_name" in quality.dirty_reasons


def test_planner_quality_does_not_penalize_allocation_amount_lowering() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Split 20k between Adebayo and Mum",
            parameters=TransferTaskParameters(
                amount="20k",
                recipient_allocations=[
                    RecipientAllocation(recipient_name="Adebayo", amount=10000),
                    RecipientAllocation(recipient_name="Mum", amount=10000),
                ],
            ),
            risk="MONEY_MOVE",
        )
    )

    _normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Split 20k between Adebayo and Mum",
    )

    assert quality.clean


def test_transfer_normalizer_does_not_infer_bank_from_batch_alias_text() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Send 2k each to Tolu Access and Tolu GTB",
            parameters=TransferTaskParameters(amount=2000),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Send 2k each to Tolu Access and Tolu GTB",
    )

    assert quality.clean
    assert normalized.tasks[0].parameters.bank_name is None


def test_planner_quality_clean_for_source_aware_transfer_slots() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Use GTBank to send 5k to Tolu Access for lunch",
            parameters=TransferTaskParameters(
                amount=5000,
                source_bank_name="GTBank",
                recipient_name="Tolu Access",
                bank_name=None,
                narration="Lunch",
            ),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Use GTBank to send 5k to Tolu Access for lunch",
    )

    assert quality.clean
    assert normalized.tasks[0].parameters.source_bank_name == "GTBank"
    assert normalized.tasks[0].parameters.bank_name is None


def test_planner_quality_clean_for_shortened_source_aware_task_instruction() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="send 5k to Tolu Access for lunch",
            parameters=TransferTaskParameters(
                amount=5000,
                source_bank_name="GTBank",
                recipient_name="Tolu Access",
                bank_name=None,
                narration="Lunch",
            ),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Use GTBank to send 5k to Tolu Access for lunch",
    )

    assert quality.clean
    assert normalized.tasks[0].parameters.source_bank_name == "GTBank"
    assert normalized.tasks[0].parameters.bank_name is None


def test_planner_quality_dirty_when_source_bank_is_mis_slotted_as_destination_bank() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Use GTBank to send 5k to Tolu Access for lunch",
            parameters=TransferTaskParameters(
                amount=5000,
                recipient_name="Tolu Access",
                bank_name="GTBank",
                narration="Lunch",
            ),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Use GTBank to send 5k to Tolu Access for lunch",
    )

    assert not quality.clean
    assert "normalizer.transfer.bank_name" in quality.dirty_reasons
    assert normalized.tasks[0].parameters.source_bank_name == "GTBank"
    assert normalized.tasks[0].parameters.bank_name is None


def test_planner_quality_dirty_when_source_first_alias_suffix_becomes_bank_name() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Use GTBank to send 5k to Tolu Access for lunch",
            parameters=TransferTaskParameters(
                amount=5000,
                source_bank_name="GTBank",
                recipient_name="Tolu Access",
                bank_name="Access Bank",
                narration="Lunch",
            ),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Use GTBank to send 5k to Tolu Access for lunch",
    )

    assert not quality.clean
    assert "normalizer.transfer.bank_name" in quality.dirty_reasons
    assert normalized.tasks[0].parameters.bank_name is None


def test_planner_quality_dirty_when_data_plan_is_patched() -> None:
    planner_output = _planner_output(
            make_planned_task(
                task_id="d1",
                action="buy_data",
                executor="data",
                instruction="Buy 1GB MTN data for me",
                parameters=DataTaskParameters(amount="1GB", network="MTN", is_self=True),
                risk="MONEY_MOVE",
            )
    )

    _normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Buy 1GB MTN data for me",
    )

    assert not quality.clean
    assert "normalizer.data.plan" in quality.dirty_reasons
    assert "normalizer.data.amount" in quality.dirty_reasons


def test_planner_quality_clean_for_data_phone_alias_mirror() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="d1",
            action="buy_data",
            executor="data",
            instruction="Buy 1GB MTN data for 08162511023",
            parameters=DataTaskParameters(plan="1GB", network="MTN", recipient_phone="08162511023"),
            risk="MONEY_MOVE",
        )
    )

    normalized, quality = normalize_planner_transaction_output_with_quality(
        planner_output,
        "Buy 1GB MTN data for 08162511023",
    )

    assert quality.clean
    assert normalized.tasks[0].parameters.phone == "08162511023"


def test_planner_quality_dirty_for_text_derived_fanout_repair() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Send 2k each to Tolu Access and Tolu GTB",
            parameters=TransferTaskParameters(amount=2000, bank_name="GTBank"),
            risk="MONEY_MOVE",
        )
    )

    result = _postprocess_planner_tasks_with_quality(
        planner_output,
        "Send 2k each to Tolu Access and Tolu GTB",
        quality_report=PlannerQualityReport(),
    )

    assert not result.quality_report.clean
    assert "postprocess.transfer.text_derived_fanout" in result.quality_report.dirty_reasons


def test_planner_quality_dirty_when_mixed_source_bank_is_propagated_to_sibling_task() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to adebayo",
                parameters=TransferTaskParameters(amount=10000, recipient_name="adebayo"),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t2",
                action="buy_airtime",
                executor="airtime",
                instruction="buy me 2k airtime from my gtb",
                parameters=AirtimeTaskParameters(amount=2000, is_self=True, source_bank_name="GTBank"),
                risk="MONEY_MOVE",
            ),
        ],
        confidence=0.98,
        detected_language="English",
    )

    result = _postprocess_planner_tasks_with_quality(
        planner_output,
        "Send 10k to adebayo and buy me 2k airtime from my gtb",
        quality_report=PlannerQualityReport(),
    )

    assert not result.quality_report.clean
    assert "postprocess.mixed_source_bank_propagation" in result.quality_report.dirty_reasons
    assert [task.parameters.source_bank_name for task in result.planner_output.tasks] == ["GTBank", "GTBank"]


def test_planner_quality_clean_when_mixed_source_bank_is_already_on_every_task() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        tasks=[
            make_planned_task(
                task_id="t1",
                action="send_money",
                executor="transfer",
                instruction="Send 10k to adebayo",
                parameters=TransferTaskParameters(
                    amount=10000,
                    recipient_name="adebayo",
                    source_bank_name="GTBank",
                ),
                risk="MONEY_MOVE",
            ),
            make_planned_task(
                task_id="t2",
                action="buy_airtime",
                executor="airtime",
                instruction="buy me 2k airtime from my gtb",
                parameters=AirtimeTaskParameters(amount=2000, is_self=True, source_bank_name="GTBank"),
                risk="MONEY_MOVE",
            ),
        ],
        confidence=0.98,
        detected_language="English",
    )

    result = _postprocess_planner_tasks_with_quality(
        planner_output,
        "Send 10k to adebayo and buy me 2k airtime from my gtb",
        quality_report=PlannerQualityReport(),
    )

    assert result.quality_report.clean
    assert [task.parameters.source_bank_name for task in result.planner_output.tasks] == ["GTBank", "GTBank"]


def test_planner_quality_dirty_when_clause_repair_adds_missing_transfer_task() -> None:
    planner_output = PlannerOutput(
        primary_intent="mixed",
        clauses=[
            PlannerClause(
                clause_index=1,
                text="Send 10 to adebayo",
                intent_family="transfer",
                extracted_fields={"amount": "10", "recipient_name": "adebayo", "source_bank_name": "GTBank"},
            ),
            PlannerClause(
                clause_index=2,
                text="buy me 2k airtime from my gtb",
                intent_family="airtime",
                task_ids=["t_airtime"],
            ),
        ],
        tasks=[
            make_planned_task(
                task_id="t_airtime",
                action="buy_airtime",
                executor="airtime",
                instruction="buy me 2k airtime from my gtb",
                source_clause_index=2,
                parameters=AirtimeTaskParameters(amount=2000, is_self=True, source_bank_name="GTBank"),
                risk="MONEY_MOVE",
            )
        ],
        confidence=0.98,
        detected_language="English",
    )

    result = _postprocess_planner_tasks_with_quality(
        planner_output,
        "Send 10 to adebayo and buy me 2k airtime from my gtb",
        quality_report=PlannerQualityReport(),
    )

    assert [task.executor for task in result.planner_output.tasks] == ["transfer", "airtime"]
    assert not result.quality_report.clean
    assert "postprocess.clause_repair" in result.quality_report.dirty_reasons


def test_planner_quality_clean_for_recipient_allocation_lowering() -> None:
    planner_output = _planner_output(
        make_planned_task(
            task_id="t1",
            action="send_money",
            executor="transfer",
            instruction="Send 2k each to Tolu Access and Tolu GTB",
            parameters=TransferTaskParameters(
                amount=4000,
                recipient_allocations=[
                    RecipientAllocation(recipient_name="Tolu Access", amount=2000),
                    RecipientAllocation(recipient_name="Tolu GTB", amount=2000),
                ],
            ),
            risk="MONEY_MOVE",
        )
    )

    result = _postprocess_planner_tasks_with_quality(
        planner_output,
        "Send 2k each to Tolu Access and Tolu GTB",
        quality_report=PlannerQualityReport(),
    )

    assert result.quality_report.clean
    assert [task.parameters.recipient_name for task in result.planner_output.tasks] == ["Tolu Access", "Tolu GTB"]
