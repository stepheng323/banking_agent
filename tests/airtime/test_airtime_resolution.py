from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.chat.src.agent.graphs.airtime.models.types import AirtimeContext, AirtimeGates, AirtimePayload
from apps.chat.src.agent.graphs.airtime.nodes.resolution import ResolutionStep
from apps.chat.src.agent.orchestrator.models.domain import TransactionOutcome


@pytest.mark.asyncio
async def test_airtime_resolution_normalizes_phone_and_infers_network() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(recipient_phone="816 251 1023")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"recipient_phone": "08162511023", "network": "MTN"}


@pytest.mark.asyncio
async def test_airtime_resolution_asks_when_provided_network_conflicts_with_phone_prefix() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(recipient_phone="08162511023", network="Airtel")
    context = AirtimeContext(phone_number="2348000000000", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["network"]
    assert "Which network is 08162511023?" in (result.prompt or "")
    assert "Invalid network." in (result.update_message or "")
    assert result.details == {
        "conflict": "NETWORK_PHONE_MISMATCH",
        "inferred_network": "MTN",
        "provided_network": "AIRTEL",
    }


@pytest.mark.asyncio
async def test_airtime_resolution_self_uses_context_phone_when_recipient_missing() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(is_self=True)
    context = AirtimeContext(phone_number="+2348162511023", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {
        "recipient_phone": "08162511023",
        "recipient_name": "My Number",
        "is_self": True,
        "network": "MTN",
    }


@pytest.mark.asyncio
async def test_airtime_resolution_self_does_not_override_explicit_recipient_phone() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(is_self=True, recipient_phone="08081234567")
    context = AirtimeContext(phone_number="+2348162511023", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"recipient_phone": "08081234567", "is_self": False, "network": "AIRTEL"}


@pytest.mark.asyncio
async def test_airtime_resolution_bare_purchase_defaults_to_user_line() -> None:
    step = ResolutionStep()
    payload = AirtimePayload()
    context = AirtimeContext(phone_number="+2348162511023", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {
        "recipient_phone": "08162511023",
        "recipient_name": "My Number",
        "is_self": True,
        "network": "MTN",
    }


@pytest.mark.asyncio
async def test_airtime_resolution_explicit_network_does_not_relabel_mismatched_user_line() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(network="Airtel")
    context = AirtimeContext(phone_number="+2348162511023", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"network": "AIRTEL"}


@pytest.mark.asyncio
async def test_airtime_resolution_saved_mobile_beneficiary_sets_phone_network_and_id() -> None:
    beneficiary_id = uuid4()
    step = ResolutionStep()
    payload = AirtimePayload(recipient_name="Mum")
    context = AirtimeContext(
        phone_number="+2348162511023",
        language="en",
        beneficiaries=[
            {
                "id": beneficiary_id,
                "beneficiary_type": "airtime",
                "alias": "Mum",
                "account_name": "Mum",
                "account_number": "08081234567",
                "bank_name": "Airtel",
            }
        ],
    )
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {
        "recipient_phone": "08081234567",
        "recipient_name": "Mum",
        "beneficiary_id": str(beneficiary_id),
        "is_self": False,
        "network": "AIRTEL",
    }


@pytest.mark.asyncio
async def test_airtime_resolution_requested_network_does_not_reuse_mismatched_saved_beneficiary() -> None:
    beneficiary_id = uuid4()
    step = ResolutionStep()
    payload = AirtimePayload(amount=1000, network="Airtel", recipient_name="Mum")
    context = AirtimeContext(
        phone_number="+2348162511023",
        language="en",
        beneficiaries=[
            {
                "id": beneficiary_id,
                "beneficiary_type": "airtime",
                "alias": "Mum",
                "account_name": "Mum",
                "account_number": "08162511023",
                "bank_name": "MTN",
            }
        ],
    )
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.NEEDS_INPUT
    assert result.required_fields == ["recipient_phone"]
    assert result.prompt == "Got ₦1,000.00 Airtel airtime. Which Airtel line should I buy it for?"
    assert result.patch["network"] == "AIRTEL"
    assert result.patch["is_self"] is False
    assert "recipient_phone" not in result.patch
    assert "beneficiary_id" not in result.patch
    assert result.details == {
        "conflict": "NETWORK_BENEFICIARY_MISMATCH",
        "beneficiary_network": "MTN",
        "provided_network": "AIRTEL",
    }


@pytest.mark.asyncio
async def test_airtime_resolution_ignores_transfer_beneficiary_with_same_alias() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(recipient_name="Mum")
    context = AirtimeContext(
        phone_number="+2348162511023",
        language="en",
        beneficiaries=[
            {
                "id": uuid4(),
                "beneficiary_type": "transfer",
                "alias": "Mum",
                "account_name": "Mum",
                "account_number": "2010000001",
                "bank_name": "GTBank",
            }
        ],
    )
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {}
