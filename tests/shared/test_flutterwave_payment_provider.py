import pytest

from shared.clients.providers.flutterwave.payment import FlutterwavePaymentProvider


class _FakeFlutterwaveClient:
    is_configured = True
    use_sandbox = True

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    async def request(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
        timeout: float = 30.0,
        max_retries: int = 1,
    ) -> dict:
        self.requests.append(
            {
                "method": method,
                "endpoint": endpoint,
                "payload": payload,
                "timeout": timeout,
                "max_retries": max_retries,
            }
        )
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_flutterwave_initiate_transfer_posts_real_transfer_payload() -> None:
    client = _FakeFlutterwaveClient(
        [
            {
                "success": True,
                "data": {
                    "id": 12345,
                    "reference": "idem-1",
                    "status": "NEW",
                    "amount": 5000,
                    "currency": "NGN",
                },
                "message": "Transfer queued",
            }
        ]
    )
    provider = FlutterwavePaymentProvider(client=client)  # type: ignore[arg-type]

    result = await provider.initiate_transfer(
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="044",
        narration="Test payout",
        reference="idem-1",
    )

    assert client.requests == [
        {
            "method": "POST",
            "endpoint": "/v3/transfers",
            "payload": {
                "account_bank": "044",
                "account_number": "0690000032",
                "amount": 5000,
                "currency": "NGN",
                "reference": "idem-1",
                "narration": "Test payout",
            },
            "timeout": 30.0,
            "max_retries": 1,
        }
    ]
    assert result["success"] is False
    assert result["status"] == "pending"
    assert result["transaction_id"] == "12345"
    assert result["reference"] == "idem-1"


@pytest.mark.asyncio
async def test_flutterwave_success_requires_terminal_successful_status() -> None:
    client = _FakeFlutterwaveClient(
        [
            {
                "success": True,
                "data": {
                    "id": "trf-1",
                    "reference": "idem-2",
                    "status": "SUCCESSFUL",
                },
            }
        ]
    )
    provider = FlutterwavePaymentProvider(client=client)  # type: ignore[arg-type]

    result = await provider.initiate_transfer(
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="044",
        reference="idem-2",
    )

    assert result["success"] is True
    assert result["status"] == "successful"
    assert result["transaction_id"] == "trf-1"


@pytest.mark.asyncio
async def test_flutterwave_failed_transfer_maps_to_failed() -> None:
    client = _FakeFlutterwaveClient(
        [
            {
                "success": True,
                "data": {
                    "id": "trf-2",
                    "reference": "idem-3",
                    "status": "FAILED",
                    "complete_message": "Invalid recipient",
                },
            }
        ]
    )
    provider = FlutterwavePaymentProvider(client=client)  # type: ignore[arg-type]

    result = await provider.initiate_transfer(
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="044",
        reference="idem-3",
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"] == "Invalid recipient"


@pytest.mark.asyncio
async def test_flutterwave_duplicate_reference_looks_up_existing_transfer() -> None:
    client = _FakeFlutterwaveClient(
        [
            {"success": False, "error": "Duplicate Reference", "status_code": 400},
            {
                "success": True,
                "data": [
                    {
                        "id": "trf-duplicate",
                        "reference": "idem-4",
                        "status": "SUCCESSFUL",
                    }
                ],
            },
        ]
    )
    provider = FlutterwavePaymentProvider(client=client)  # type: ignore[arg-type]

    result = await provider.initiate_transfer(
        amount=5000,
        recipient_account_number="8162511023",
        recipient_bank_code="044",
        reference="idem-4",
    )

    assert client.requests[0]["endpoint"] == "/v3/transfers"
    assert client.requests[1]["method"] == "GET"
    assert client.requests[1]["endpoint"] == "/v3/transfers?reference=idem-4&page_size=1"
    assert result["success"] is True
    assert result["status"] == "successful"
    assert result["transaction_id"] == "trf-duplicate"


@pytest.mark.asyncio
async def test_flutterwave_get_transfer_status_maps_provider_status() -> None:
    client = _FakeFlutterwaveClient(
        [
            {"success": True, "data": {"id": "trf-success", "status": "SUCCESSFUL"}},
            {"success": True, "data": {"id": "trf-pending", "status": "PENDING"}},
            {"success": True, "data": {"id": "trf-failed", "status": "FAILED"}},
        ]
    )
    provider = FlutterwavePaymentProvider(client=client)  # type: ignore[arg-type]

    successful = await provider.get_transfer_status("trf-success")
    pending = await provider.get_transfer_status("trf-pending")
    failed = await provider.get_transfer_status("trf-failed")

    assert successful["success"] is True
    assert successful["status"] == "successful"
    assert pending["success"] is False
    assert pending["status"] == "pending"
    assert failed["success"] is False
    assert failed["status"] == "failed"
