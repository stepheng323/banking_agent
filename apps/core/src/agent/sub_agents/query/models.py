"""Pydantic models for query service."""

from typing import Literal

from pydantic import BaseModel, Field


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
        "breakdown",
        "affordability",
    ] = Field(default="transaction_list")

    transaction_type: Literal["debit", "credit", "both"] = Field(default="both")
    date_range: DateRange | None = None
    narration_filter: str | None = Field(
        default=None, description="Filter by counterparty/merchant name"
    )
    group_by: Literal["category", "recipient", "sender", "date", "none"] = Field(default="none")
    limit: int = Field(default=10, ge=1, le=100)

    # Affordability fields
    amount_check: float | None = Field(
        default=None, description="Amount to check for affordability"
    )
    analysis_type: Literal["immediate", "relative", "simulated", "remainder"] = Field(
        default="immediate", description="Type of affordability analysis"
    )
    item_name: str | None = Field(default=None, description="Product name like 'MacBook Pro'")
    projection_months: int | None = Field(default=None, ge=1, le=6, description="Months to project")
    pool_accounts: bool = Field(default=False, description="Consider all linked accounts")


class BalanceResponse(BaseModel):
    """Balance inquiry response."""

    balance_naira: float
    ledger_balance_naira: float
    currency: str = "NGN"
    account_name: str | None = None
    bank_name: str | None = None


class TransactionItem(BaseModel):
    """Single transaction item."""

    date: str
    narration: str
    amount_naira: float
    type: Literal["debit", "credit"]
    category: str | None = None
    counterparty: str | None = None


class TransactionSummary(BaseModel):
    """Summary of transactions."""

    total_naira: float
    transaction_count: int
    transaction_type: str
    date_range: DateRange | None = None


class TopCounterparty(BaseModel):
    """Top recipient or sender."""

    name: str
    total_naira: float
    transaction_count: int


class AnalyticsResult(BaseModel):
    """Analytics result for top recipients/senders/categories."""

    items: list[TopCounterparty]
    total_naira: float
    date_range: DateRange | None = None
