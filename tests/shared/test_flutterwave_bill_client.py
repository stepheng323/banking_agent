from typing import Any

import pytest

from shared.clients.providers.flutterwave.bill import FlutterwaveBillsClient


class _FlutterwaveClientStub:
    base_url = "https://api.flutterwave.com"
    is_configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.calls.append((method, endpoint, payload))
        if endpoint == "/v3/bills/MOBILEDATA/billers?country=NG":
            return {
                "success": True,
                "data": [
                    {"name": "MTN DATA BUNDLE", "biller_code": "BIL104"},
                    {"name": "GLO DATA BUNDLE", "biller_code": "BIL105"},
                    {"name": "AIRTEL DATA BUNDLE", "biller_code": "BIL106"},
                    {"name": "9MOBILE DATA BUNDLE", "biller_code": "BIL107"},
                ],
            }
        if endpoint == "/v3/billers/BIL106/items":
            return {
                "success": True,
                "data": [
                    {
                        "item_code": "MD120",
                        "biller_code": "BIL106",
                        "name": "AIRTEL DATA BUNDLE",
                        "biller_name": "AIRTEL 750 MB data bundle",
                        "short_name": "AIRTEL 750 MB data bundle",
                        "amount": 500,
                        "validity_period": "7",
                        "is_data": True,
                    }
                ],
            }
        if endpoint == "/v3/billers/BIL104/items/MD108/payment":
            return {"success": True, "data": {"reference": "ref-1", "status": "successful"}}
        return {"success": False, "error": "unexpected endpoint"}


@pytest.mark.asyncio
async def test_flutterwave_get_data_plans_discovers_biller_and_preserves_validity() -> None:
    client = _FlutterwaveClientStub()
    biller = FlutterwaveBillsClient(client=client)

    result = await biller.get_data_plans("airtel")

    assert result["success"] is True
    assert result["network"] == "AIRTEL"
    assert result["plans"] == [
        {
            "item_code": "MD120",
            "name": "AIRTEL 750 MB data bundle",
            "amount": 500,
            "biller_code": "BIL106",
            "biller_name": "AIRTEL 750 MB data bundle",
            "short_name": "AIRTEL 750 MB data bundle",
            "validity_period": "7",
            "category_name": None,
            "group_name": None,
            "is_data": True,
            "raw_item": {
                "item_code": "MD120",
                "biller_code": "BIL106",
                "biller_name": "AIRTEL 750 MB data bundle",
                "short_name": "AIRTEL 750 MB data bundle",
                "amount": 500,
                "validity_period": "7",
                "is_data": True,
            },
        }
    ]
    assert ("GET", "/v3/bills/MOBILEDATA/billers?country=NG", None) in client.calls


@pytest.mark.asyncio
async def test_flutterwave_purchase_data_sends_exact_catalog_amount() -> None:
    client = _FlutterwaveClientStub()
    biller = FlutterwaveBillsClient(client=client)

    result = await biller.purchase_data(
        plan_code="MD108",
        recipient_phone="2348162511023",
        network="MTN",
        amount=2000,
        reference="idem-1",
    )

    assert result["success"] is True
    assert client.calls[-1] == (
        "POST",
        "/v3/billers/BIL104/items/MD108/payment",
        {
            "country": "NG",
            "customer_id": "08162511023",
            "reference": "idem-1",
            "amount": 2000,
        },
    )
