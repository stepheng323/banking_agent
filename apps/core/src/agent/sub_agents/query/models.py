"""Pydantic models for query service."""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime


class DateRange(BaseModel):
    """Date range for query filtering."""
    start: str = Field(..., description="Start date in YYYY-MM-DD format")
    end: str = Field(..., description="End date in YYYY-MM-DD format")


class QueryParams(BaseModel):
    """Parsed query parameters from natural language."""
    query_type: Literal[
        "balance",
        "total_spent",
        "total_received",
        "transaction_list",
        "search",
        "top_recipient",
        "top_sender",
        "breakdown"
    ] = Field(default="transaction_list")
    
    transaction_type: Literal["debit", "credit", "both"] = Field(default="both")
    date_range: Optional[DateRange] = None
    narration_filter: Optional[str] = Field(default=None, description="Filter by counterparty/merchant name")
    group_by: Literal["category", "recipient", "sender", "date", "none"] = Field(default="none")
    limit: int = Field(default=10, ge=1, le=100)


class BalanceResponse(BaseModel):
    """Balance inquiry response."""
    balance_naira: float
    ledger_balance_naira: float
    currency: str = "NGN"
    account_name: Optional[str] = None
    bank_name: Optional[str] = None


class TransactionItem(BaseModel):
    """Single transaction item."""
    date: str
    narration: str
    amount_naira: float
    type: Literal["debit", "credit"]
    category: Optional[str] = None
    counterparty: Optional[str] = None


class TransactionSummary(BaseModel):
    """Summary of transactions."""
    total_naira: float
    transaction_count: int
    transaction_type: str
    date_range: Optional[DateRange] = None


class TopCounterparty(BaseModel):
    """Top recipient or sender."""
    name: str
    total_naira: float
    transaction_count: int


class AnalyticsResult(BaseModel):
    """Analytics result for top recipients/senders/categories."""
    items: List[TopCounterparty]
    total_naira: float
    date_range: Optional[DateRange] = None
