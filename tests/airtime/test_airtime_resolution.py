from types import SimpleNamespace

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
    assert result.patch == {"recipient_phone": "08162511023", "recipient_name": "My Number", "network": "MTN"}


@pytest.mark.asyncio
async def test_airtime_resolution_self_does_not_override_explicit_recipient_phone() -> None:
    step = ResolutionStep()
    payload = AirtimePayload(is_self=True, recipient_phone="08081234567")
    context = AirtimeContext(phone_number="+2348162511023", language="en")
    gates = AirtimeGates()

    result = await step.execute(payload, context, gates, SimpleNamespace())

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {"recipient_phone": "08081234567", "network": "AIRTEL"}
