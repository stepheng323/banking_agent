import pytest
from decimal import Decimal
from typing import Any

from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    CoverageStatus,
)
from banking.transactions.query.services.analysis.relationships import detect_probable_duplicates


def test_detect_probable_duplicates_exact_matches() -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date=None,
        end_date=None,
        coverage_status=CoverageStatus.COMPLETE,
        rows=[
            {
                "transaction_id": "1",
                "date": "2023-10-01T10:00:00Z",
                "amount": 5000.0,
                "direction": "debit",
                "counterparty": "Merchant A",
            },
            {
                "transaction_id": "2",
                "date": "2023-10-01T10:00:00Z",
                "amount": 5000.0,
                "direction": "debit",
                "counterparty": "Merchant A",
            },
            {
                "transaction_id": "3",
                "date": "2023-10-01T14:00:00Z",
                "amount": 1000.0,
                "direction": "credit",
                "narration": "Salary",
            },
        ],
    )
    
    result = detect_probable_duplicates(dataset, min_confidence=0.8)
    
    assert result.available is True
    assert len(result.candidates) == 1
    
    group = result.candidates[0]
    assert len(group.transactions) == 2
    assert group.redundant_value == Decimal("5000.0")
    assert result.total_redundant_value == Decimal("5000.0")


def test_detect_probable_duplicates_none_found() -> None:
    dataset = AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date=None,
        end_date=None,
        coverage_status=CoverageStatus.COMPLETE,
        rows=[
            {
                "transaction_id": "1",
                "date": "2023-10-01T10:00:00Z",
                "amount": 5000.0,
                "direction": "debit",
                "counterparty": "Merchant A",
            },
            {
                "transaction_id": "2",
                "date": "2023-10-02T10:00:00Z", # Different date
                "amount": 5000.0,
                "direction": "debit",
                "counterparty": "Merchant A",
            },
        ],
    )
    
    result = detect_probable_duplicates(dataset, min_confidence=0.8)
    assert len(result.candidates) == 0
    assert result.total_redundant_value == Decimal("0")
