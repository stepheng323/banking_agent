"""Mono API Client for bank data access."""

from typing import Optional, List, Dict, Any
import aiohttp
from datetime import datetime

from shared.utils.logging import get_logger

logger = get_logger(__name__)


class MonoClient:
    """Client for interacting with Mono API v2."""

    def __init__(self, api_key: str, base_url: str = "https://api.withmono.com"):
        """
        Initialize Mono client.

        Args:
            api_key: Mono Secret Key
            base_url: Base URL for Mono API
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "mono-sec-key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json"
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        real_time: bool = False
    ) -> Dict[str, Any]:
        """
        Make an API request to Mono.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            real_time: If True, request real-time data

        Returns:
            API response data
        """
        url = f"{self.base_url}{endpoint}"
        headers = self.headers.copy()
        
        if real_time:
            headers["x-real-time"] = "true"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    params=params
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data
                    else:
                        error_text = await resp.text()
                        logger.error(
                            "mono_api_error",
                            status=resp.status,
                            endpoint=endpoint,
                            error=error_text[:200]
                        )
                        return {"error": error_text, "status": resp.status}
        except aiohttp.ClientError as e:
            logger.error("mono_connection_error", endpoint=endpoint, error=str(e))
            return {"error": str(e), "status": 0}

    async def get_balance(
        self,
        account_id: str,
        real_time: bool = True
    ) -> Dict[str, Any]:
        """
        Get account balance.

        Args:
            account_id: The Mono account ID
            real_time: If True, fetch real-time balance (may be slower)

        Returns:
            Balance data including available and ledger balance
        """
        endpoint = f"/v2/accounts/{account_id}/balance"
        result = await self._request("GET", endpoint, real_time=real_time)
        
        if "error" in result:
            return result
        
        data = result.get("data", result)
        return {
            "balance_kobo": data.get("available_balance", 0),
            "balance_naira": data.get("available_balance", 0) / 100,
            "ledger_balance_kobo": data.get("ledger_balance", 0),
            "ledger_balance_naira": data.get("ledger_balance", 0) / 100,
            "currency": data.get("currency", "NGN"),
            "account_id": account_id
        }

    async def get_transactions(
        self,
        account_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        transaction_type: Optional[str] = None,
        narration: Optional[str] = None,
        limit: int = 50,
        paginate: bool = True,
        real_time: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch transactions for an account.

        Args:
            account_id: The Mono account ID
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            transaction_type: 'debit' or 'credit'
            narration: Filter by narration (client-side filtering)
            limit: Number of transactions to return
            paginate: If False, return all transactions in one request
            real_time: If True, fetch real-time transactions

        Returns:
            List of transaction objects
        """
        endpoint = f"/v2/accounts/{account_id}/transactions"
        
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if transaction_type:
            params["type"] = transaction_type
        if not paginate:
            params["paginate"] = "false"
        if limit:
            params["limit"] = str(limit)

        result = await self._request("GET", endpoint, params=params, real_time=real_time)
        
        if "error" in result:
            logger.warning("mono_transactions_error", account_id=account_id, error=result.get("error"))
            return []
        
        # Extract transactions from response
        transactions = result.get("data", [])
        if isinstance(result.get("data"), dict):
            transactions = result["data"].get("transactions", [])
        
        # Client-side filtering by narration if specified
        if narration and transactions:
            narration_lower = narration.lower()
            transactions = [
                t for t in transactions
                if narration_lower in t.get("narration", "").lower()
            ]
        
        return transactions[:limit] if limit else transactions

    async def get_account(self, account_id: str) -> Dict[str, Any]:
        """
        Get account details.

        Args:
            account_id: The Mono account ID

        Returns:
            Account details including name, type, institution
        """
        endpoint = f"/v2/accounts/{account_id}"
        result = await self._request("GET", endpoint)
        
        if "error" in result:
            return result
        
        return result.get("data", result)
