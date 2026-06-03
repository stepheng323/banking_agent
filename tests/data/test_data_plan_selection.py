from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome
from banking.bills.data.models.extraction import DataExtractionResult, DataPurchaseEntities
from banking.bills.data.models.plans import DataPlan
from banking.bills.data.models.types import DataContext, DataGates, DataPayload
from banking.bills.data.nodes.extraction import ExtractionStep
from banking.bills.data.nodes.plan_selection import DataPlanQueryStep, DataPlanSelectionStep
from banking.bills.data.plans.service import DataPlanService
from banking.bills.data.worker import DataWorker


class _PlanServiceStub:
    def __init__(self, plans: list[DataPlan]) -> None:
        self.plans = plans
        self.calls: list[str] = []

    async def get_plans(self, network: str) -> list[DataPlan]:
        self.calls.append(network)
        return self.plans


class _BillProviderStub:
    async def get_data_plans(self, network: str) -> dict:
        return {
            "success": True,
            "plans": [
                {
                    "item_code": "MD108",
                    "biller_code": "BIL104",
                    "biller_name": "MTN 3.5 GB",
                    "short_name": "MTN 3.5 GB",
                    "amount": 2000,
                    "validity_period": "30",
                    "raw_item": {"item_code": "MD108", "validity_period": "30"},
                }
            ],
        }


class _DuplicateBillProviderStub:
    async def get_data_plans(self, network: str) -> dict:
        del network
        return {
            "success": True,
            "plans": [
                {
                    "item_code": "MD501",
                    "biller_code": "BIL104",
                    "biller_name": "MTN 5 GB data bundle",
                    "short_name": "MTN 5 GB data bundle",
                    "amount": 3500,
                    "validity_period": "30",
                },
                {
                    "item_code": "MD502",
                    "biller_code": "BIL_ALT",
                    "biller_name": "MTN 5GB data bundle",
                    "short_name": "MTN 5GB data bundle",
                    "amount": 3500,
                    "validity_period": "30",
                },
            ],
        }


class _ExtractorStub:
    def __init__(self, result: DataExtractionResult) -> None:
        self.result = result

    async def extract(self, _message: str, smart_context: dict | None = None) -> DataExtractionResult:
        del smart_context
        return self.result


def _plans() -> list[DataPlan]:
    return [
        DataPlan(
            item_code="MD106",
            biller_code="BIL104",
            name="MTN 750 MB",
            network="MTN",
            amount=500,
            size_gb=0.75,
            validity_days=7,
        ),
        DataPlan(
            item_code="MD107",
            biller_code="BIL104",
            name="MTN 1.5 GB",
            network="MTN",
            amount=1000,
            size_gb=1.5,
            validity_days=30,
        ),
        DataPlan(
            item_code="MD108",
            biller_code="BIL104",
            name="MTN 3.5 GB",
            network="MTN",
            amount=2000,
            size_gb=3.5,
            validity_days=30,
        ),
    ]


def _airtel_plans() -> list[DataPlan]:
    return [
        DataPlan(
            item_code="AD101",
            biller_code="BIL106",
            name="AIRTEL 250 MB Daily",
            network="AIRTEL",
            amount=100,
            size_gb=0.25,
            validity_days=1,
            tags=["daily"],
        ),
        DataPlan(
            item_code="AD130",
            biller_code="BIL106",
            name="AIRTEL 3 GB Monthly",
            network="AIRTEL",
            amount=1500,
            size_gb=3,
            validity_days=30,
            tags=["monthly"],
        ),
    ]


def _same_size_different_validity_plans() -> list[DataPlan]:
    return [
        DataPlan(
            item_code="MD5_DAILY",
            biller_code="BIL104",
            name="MTN 5 GB Daily",
            network="MTN",
            amount=1500,
            size_gb=5,
            validity_days=1,
            tags=["daily"],
        ),
        DataPlan(
            item_code="MD5_MONTHLY",
            biller_code="BIL104",
            name="MTN 5 GB Monthly",
            network="MTN",
            amount=3500,
            size_gb=5,
            validity_days=30,
            tags=["monthly"],
        ),
    ]


@pytest.mark.asyncio
async def test_data_plan_service_parses_flutterwave_name_size_and_validity() -> None:
    service = DataPlanService(_BillProviderStub(), redis_client=None)

    plans = await service.get_plans("mtn")

    assert len(plans) == 1
    assert plans[0].item_code == "MD108"
    assert plans[0].name == "MTN 3.5 GB"
    assert plans[0].size_gb == 3.5
    assert plans[0].validity_days == 30
    assert "monthly" in plans[0].tags
    assert plans[0].raw_metadata["validity_period"] == "30"


@pytest.mark.asyncio
async def test_data_plan_service_dedupes_equivalent_provider_items() -> None:
    service = DataPlanService(_DuplicateBillProviderStub(), redis_client=None)

    plans = await service.get_plans("mtn")

    assert len(plans) == 1
    assert plans[0].item_code == "MD501"
    assert plans[0].name == "MTN 5 GB data bundle"
    assert plans[0].amount == 3500
    assert plans[0].validity_days == 30


@pytest.mark.asyncio
async def test_data_plan_selection_budget_selects_most_data_within_budget() -> None:
    payload = DataPayload(network="MTN", amount=2000, target_phone="08162511023")
    step = DataPlanSelectionStep("buy best 2k MTN data")

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.plan_code == "MD108"
    assert payload.plan_name == "MTN 3.5 GB"
    assert payload.amount == 2000


@pytest.mark.asyncio
async def test_data_plan_selection_bare_purchase_uses_user_line_and_asks_preference() -> None:
    payload = DataPayload()
    plan_service = _PlanServiceStub(_plans())
    step = DataPlanSelectionStep("I want to buy data")

    result = await step.run(
        payload,
        DataContext(phone_number="2348162511023", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=plan_service),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["data_plan_preference"]
    assert result.prompt == "Sure. I'll use your MTN line. What budget or data size should I use?"
    assert payload.target_phone == "08162511023"
    assert payload.network == "MTN"
    assert payload.is_self is True
    assert plan_service.calls == []


@pytest.mark.asyncio
async def test_data_plan_selection_explicit_network_does_not_relabel_mismatched_user_line() -> None:
    payload = DataPayload(network="AIRTEL")
    plan_service = _PlanServiceStub(_airtel_plans())
    step = DataPlanSelectionStep("I want to buy Airtel data")

    result = await step.run(
        payload,
        DataContext(phone_number="2348162511023", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=plan_service),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["target_phone", "data_plan_preference"]
    assert result.prompt == "Sure. Which Airtel line should I buy for, and what budget or data size should I use?"
    assert payload.target_phone is None
    assert payload.network == "AIRTEL"
    assert payload.is_self is False
    assert plan_service.calls == []


@pytest.mark.asyncio
async def test_data_plan_selection_recommendation_asks_for_matching_network_line() -> None:
    payload = DataPayload(network="AIRTEL", amount=4000)
    plans = [
        *_airtel_plans(),
        DataPlan(
            item_code="AD400",
            biller_code="BIL106",
            name="AIRTEL 9GB data bundle",
            network="AIRTEL",
            amount=4000,
            size_gb=9,
            validity_days=30,
            tags=["monthly"],
        ),
    ]
    step = DataPlanSelectionStep("4k")

    result = await step.run(
        payload,
        DataContext(phone_number="2348162511023", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(plans)),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["target_phone"]
    assert (
        result.prompt
        == "I found AIRTEL 9GB data bundle for ₦4,000, valid 30 days. Which Airtel line should I buy it for?"
    )
    assert payload.plan_code == "AD400"
    assert payload.target_phone is None
    assert payload.is_self is False


@pytest.mark.asyncio
async def test_data_plan_selection_bare_purchase_unknown_user_network_asks_network_and_preference() -> None:
    payload = DataPayload()
    step = DataPlanSelectionStep("I want to buy data")

    result = await step.run(
        payload,
        DataContext(phone_number="2347001234567", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network", "data_plan_preference"]
    assert (
        result.prompt
        == "Sure. I'll use your number. Which network is it on, and what budget or data size should I use?"
    )
    assert payload.target_phone == "07001234567"
    assert payload.network is None
    assert payload.is_self is True


@pytest.mark.asyncio
async def test_data_plan_selection_explicit_unknown_number_asks_network_and_preference() -> None:
    payload = DataPayload(target_phone="07001234567")
    step = DataPlanSelectionStep("buy data for 07001234567")

    result = await step.run(
        payload,
        DataContext(phone_number="2348162511023", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network", "data_plan_preference"]
    assert result.prompt == "Sure. Which network is 07001234567 on, and what budget or data size should I use?"
    assert payload.target_phone == "07001234567"
    assert payload.network is None
    assert payload.is_self is False


@pytest.mark.asyncio
async def test_data_worker_bare_self_purchase_asks_preference_not_recipient_or_plan_list() -> None:
    worker = DataWorker(
        extractor=_ExtractorStub(DataExtractionResult(entities=DataPurchaseEntities(is_self=True))),
        bill_provider=_BillProviderStub(),
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={},
        context={"phone_number": "2348162511023", "language": "en"},
        user_message="buy me data",
    )

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["data_plan_preference"]
    assert result.prompt == "Sure. I'll use your MTN line. What budget or data size should I use?"
    assert "Which MTN data plan" not in str(result.prompt)
    assert "whose line" not in str(result.prompt).lower()
    assert result.patch["target_phone"] == "08162511023"
    assert result.patch["network"] == "MTN"
    assert result.patch["is_self"] is True


@pytest.mark.asyncio
async def test_data_worker_preference_followup_selects_catalog_plan() -> None:
    worker = DataWorker(
        extractor=_ExtractorStub(DataExtractionResult(entities=DataPurchaseEntities(budget=2000))),
        bill_provider=_BillProviderStub(),
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={"target_phone": "08162511023", "network": "MTN", "is_self": True},
        context={
            "phone_number": "2348162511023",
            "language": "en",
            "required_fields": ["data_plan_preference"],
            "accounts": [
                {
                    "id": "acc_1",
                    "bank_name": "Access Bank",
                    "account_name": "Gaines",
                    "account_number": "2010000003",
                    "is_default": True,
                }
            ],
        },
        user_message="2k",
    )

    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.patch["plan_code"] == "MD108"
    assert result.patch["amount"] == 2000
    assert result.patch["target_phone"] == "08162511023"


@pytest.mark.asyncio
async def test_data_plan_selection_explicit_number_overrides_user_number() -> None:
    payload = DataPayload(network="MTN", amount=2000)
    payload.extraction = DataExtractionResult(
        entities=DataPurchaseEntities(network="MTN", budget=2000, recipient_phone="08031234567")
    )
    step = DataPlanSelectionStep("buy best 2k MTN data for 08031234567")

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08031234567"
    assert payload.is_self is False
    assert payload.plan_code == "MD108"


@pytest.mark.asyncio
async def test_data_worker_plan_first_defaults_to_user_phone_before_confirmation() -> None:
    worker = DataWorker(
        extractor=_ExtractorStub(
            DataExtractionResult(
                entities=DataPurchaseEntities(network="MTN", budget=2000, selection_preference="most_data")
            )
        ),
        bill_provider=_BillProviderStub(),
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={},
        context={
            "phone_number": "2348000000000",
            "language": "en",
            "accounts": [
                {
                    "id": "acc_1",
                    "bank_name": "Access Bank",
                    "account_name": "Gaines",
                    "account_number": "2010000003",
                    "is_default": True,
                }
            ],
        },
        user_message="buy best 2k MTN data",
    )

    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.patch["plan_code"] == "MD108"
    assert result.patch["amount"] == 2000
    assert result.patch["target_phone"] == "08000000000"
    assert result.patch["is_self"] is True
    assert "I found MTN 3.5 GB for ₦2,000, valid 30 days" not in str(result.prompt)


@pytest.mark.asyncio
async def test_data_worker_saved_mobile_beneficiary_reaches_confirmation_without_target_prompt() -> None:
    beneficiary_id = uuid4()
    worker = DataWorker(
        extractor=_ExtractorStub(
            DataExtractionResult(
                entities=DataPurchaseEntities(
                    network="MTN",
                    budget=2000,
                    selection_preference="most_data",
                    recipient_name="Mum",
                )
            )
        ),
        bill_provider=_BillProviderStub(),
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={},
        context={
            "phone_number": "2348000000000",
            "language": "en",
            "beneficiaries": [
                {
                    "id": beneficiary_id,
                    "beneficiary_type": "data",
                    "alias": "Mum",
                    "account_name": "Mum",
                    "account_number": "08162511023",
                    "bank_name": "MTN",
                }
            ],
            "accounts": [
                {
                    "id": "acc_1",
                    "bank_name": "Access Bank",
                    "account_name": "Gaines",
                    "account_number": "2010000003",
                    "is_default": True,
                }
            ],
        },
        user_message="buy best 2k MTN data for Mum",
    )

    assert result.outcome == TransactionOutcome.NEEDS_CONFIRMATION
    assert result.patch["plan_code"] == "MD108"
    assert result.patch["amount"] == 2000
    assert result.patch["target_phone"] == "08162511023"
    assert result.patch["network"] == "MTN"
    assert result.patch["beneficiary_id"] == str(beneficiary_id)
    assert result.patch["recipient_name"] == "Mum"
    assert result.patch["is_self"] is False


@pytest.mark.asyncio
async def test_data_plan_selection_monthly_self_uses_validity_and_self_phone() -> None:
    payload = DataPayload(network="AIRTEL", validity_preference="monthly")
    payload.extraction = DataExtractionResult(
        entities=DataPurchaseEntities(network="AIRTEL", validity_preference="monthly", is_self=True)
    )
    step = DataPlanSelectionStep("buy monthly Airtel data for my line")

    result = await step.run(
        payload,
        DataContext(phone_number="2348122511023", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_airtel_plans())),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.plan_code == "AD130"
    assert payload.target_phone == "08122511023"
    assert payload.is_self is True


@pytest.mark.asyncio
async def test_data_plan_selection_usage_intent_only_ranks_when_catalog_tag_matches() -> None:
    payload = DataPayload(network="MTN", amount=2000, target_phone="08162511023", usage_intent="social")
    step = DataPlanSelectionStep("buy social bundle for 2k")
    plans = [
        DataPlan(
            item_code="MD_SOCIAL",
            biller_code="BIL104",
            name="MTN Social Bundle 1 GB",
            network="MTN",
            amount=1000,
            size_gb=1,
            validity_days=30,
            tags=["social"],
        ),
        DataPlan(
            item_code="MD_BIG",
            biller_code="BIL104",
            name="MTN 3.5 GB",
            network="MTN",
            amount=2000,
            size_gb=3.5,
            validity_days=30,
        ),
    ]

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(plans)),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.plan_code == "MD_SOCIAL"


@pytest.mark.asyncio
async def test_data_plan_selection_usage_intent_falls_back_to_value_without_catalog_match() -> None:
    payload = DataPayload(network="MTN", amount=2000, target_phone="08162511023", usage_intent="browsing")
    step = DataPlanSelectionStep("buy browsing data for 2k")

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.plan_code == "MD108"


@pytest.mark.asyncio
async def test_data_plan_selection_size_budget_conflict_keeps_budget_hard_cap() -> None:
    payload = DataPayload(network="MTN", amount=1500, size_preference="3.5GB", target_phone="08162511023")
    step = DataPlanSelectionStep("buy 3.5GB for 1500")

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert "couldn't find 3.5GB within ₦1,500" in str(result.prompt)
    assert payload.plan_code is None


@pytest.mark.asyncio
async def test_data_plan_selection_size_and_validity_selects_unambiguous_catalog_plan() -> None:
    payload = DataPayload(
        network="MTN",
        size_preference="5GB",
        validity_preference="monthly",
        target_phone="08162511023",
    )
    step = DataPlanSelectionStep("buy monthly 5GB MTN data")

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_same_size_different_validity_plans())),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.plan_code == "MD5_MONTHLY"
    assert payload.plan_name == "MTN 5 GB Monthly"
    assert payload.amount == 3500
    assert payload.data_plan_candidates == []


@pytest.mark.asyncio
async def test_data_plan_selection_numeric_choice_applies_candidate() -> None:
    payload = DataPayload(
        network="MTN",
        target_phone="08162511023",
        data_plan_candidates=[
            {
                "index": 1,
                "plan_code": "MD108",
                "plan_name": "MTN 3.5 GB",
                "network": "MTN",
                "amount": 2000,
            }
        ],
    )
    step = DataPlanSelectionStep("1")

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result.outcome == TransactionOutcome.OK
    assert payload.plan_code == "MD108"
    assert payload.plan_name == "MTN 3.5 GB"
    assert payload.amount == 2000
    assert payload.data_plan_candidates == []


@pytest.mark.asyncio
async def test_data_plan_selection_show_options_excludes_current_plan() -> None:
    payload = DataPayload(
        network="MTN",
        amount=3500,
        target_phone="08162511023",
        show_plan_options=True,
        data_plan_exclude_codes=["MD501"],
    )
    plans = [
        *_plans(),
        DataPlan(
            item_code="MD501",
            biller_code="BIL104",
            name="MTN 5 GB data bundle",
            network="MTN",
            amount=3500,
            size_gb=5,
            validity_days=30,
        ),
    ]
    step = DataPlanSelectionStep("what other plan within that range")

    result = await step.run(
        payload,
        DataContext(phone_number="2348162511023", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(plans)),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["data_plan_id"]
    assert "MTN 5 GB data bundle" not in str(result.prompt)
    assert "MTN 3.5 GB" in str(result.prompt)
    assert payload.show_plan_options is False
    assert [candidate["plan_code"] for candidate in payload.data_plan_candidates] == ["MD108", "MD107", "MD106"]


@pytest.mark.asyncio
async def test_data_plan_self_slot_reply_preserves_selected_plan() -> None:
    payload = DataPayload(
        action="buy_data",
        plan_code="MD501",
        plan_name="MTN 5 GB data bundle",
        network="MTN",
        amount=3500,
        skip_extraction=True,
    )
    context = DataContext(phone_number="2348162511023", language="en")
    worker_context = SimpleNamespace(required_fields=["target_phone"], extractor=None, plan_service=None)

    extraction_result = await ExtractionStep("For me na").run(payload, context, DataGates(), worker_context)
    plan_result = await DataPlanSelectionStep("For me na").run(payload, context, DataGates(), worker_context)

    assert extraction_result.outcome == TransactionOutcome.OK
    assert plan_result.outcome == TransactionOutcome.OK
    assert payload.target_phone == "08162511023"
    assert payload.is_self is True
    assert payload.plan_code == "MD501"
    assert payload.plan_name == "MTN 5 GB data bundle"
    assert payload.amount == 3500


@pytest.mark.asyncio
async def test_data_plan_query_exact_size_returns_catalog_price_not_confirmation() -> None:
    payload = DataPayload(network="MTN", size_preference="3.5GB")
    step = DataPlanQueryStep()

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_plans())),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.OK
    assert "MTN 3.5 GB is ₦2,000, valid for 30 days" in str(result.response)
    assert result.confirmation_summary is None
    assert result.patch["data_plan_query_results"][0]["plan_code"] == "MD108"


@pytest.mark.asyncio
async def test_data_plan_query_dedupes_equivalent_exact_size_matches() -> None:
    payload = DataPayload(network="MTN", size_preference="5GB")
    step = DataPlanQueryStep()
    plans = [
        DataPlan(
            item_code="MD501",
            biller_code="BIL104",
            name="MTN 5 GB data bundle",
            network="MTN",
            amount=3500,
            size_gb=5,
            validity_days=30,
        ),
        DataPlan(
            item_code="MD502",
            biller_code="BIL_ALT",
            name="MTN 5GB data bundle",
            network="MTN",
            amount=3500,
            size_gb=5,
            validity_days=30,
        ),
    ]

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(plans)),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.OK
    assert "I found a few matching options" not in str(result.response)
    assert "MTN 5 GB data bundle is ₦3,500, valid for 30 days" in str(result.response)
    assert len(result.patch["data_plan_query_results"]) == 1
    assert result.patch["data_plan_query_results"][0]["plan_code"] == "MD501"


@pytest.mark.asyncio
async def test_data_plan_query_size_and_validity_answers_unambiguous_catalog_plan() -> None:
    payload = DataPayload(network="MTN", size_preference="5GB", validity_preference="monthly")
    step = DataPlanQueryStep()

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(_same_size_different_validity_plans())),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.OK
    assert "MTN 5 GB Monthly is ₦3,500, valid for 30 days" in str(result.response)
    assert len(result.patch["data_plan_query_results"]) == 1
    assert result.patch["data_plan_query_results"][0]["plan_code"] == "MD5_MONTHLY"


@pytest.mark.asyncio
async def test_data_worker_allows_data_plan_query_action_through_policy_gate() -> None:
    worker = DataWorker(
        extractor=None,
        bill_provider=_BillProviderStub(),
        transaction_repo=None,
        publisher=None,
    )

    result = await worker.run(
        payload={"action": "data_plan_query", "network": "MTN", "size_preference": "3.5GB"},
        context={"phone_number": "2348000000000", "language": "en"},
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.response is not None
    assert "MTN 3.5 GB is ₦2,000, valid for 30 days" in result.response
    assert "isn't available yet" not in result.response
    assert result.patch is not None
    assert result.patch["data_plan_query_results"][0]["plan_code"] == "MD108"


@pytest.mark.asyncio
async def test_data_plan_query_explicit_validity_days_ranks_by_number_not_daily_keyword() -> None:
    payload = DataPayload(network="MTN", validity_preference="10 days")
    step = DataPlanQueryStep()
    plans = [
        DataPlan(
            item_code="MD201",
            biller_code="BIL104",
            name="MTN 500 MB",
            network="MTN",
            amount=300,
            size_gb=0.5,
            validity_days=2,
        ),
        DataPlan(
            item_code="MD202",
            biller_code="BIL104",
            name="MTN 2 GB",
            network="MTN",
            amount=1000,
            size_gb=2,
            validity_days=14,
        ),
    ]

    result = await step.run(
        payload,
        DataContext(phone_number="2348000000000", language="en"),
        DataGates(),
        SimpleNamespace(plan_service=_PlanServiceStub(plans)),
    )

    assert result is not None
    assert result.outcome == TransactionOutcome.OK
    assert result.patch["data_plan_query_results"][0]["plan_code"] == "MD202"
    assert "1. MTN 2 GB" in str(result.response)
