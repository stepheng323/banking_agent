from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    CoverageStatus,
)
from banking.transactions.query.services.analysis.relationships import detect_probable_duplicates


def build_dataset(rows: list[dict[str, Any]]) -> AnalysisDataset:
    return AnalysisDataset(
        basis="ledger_transactions",
        period_label="current",
        start_date=date(2023, 10, 1),
        end_date=date(2023, 10, 31),
        coverage_status=CoverageStatus.COMPLETE,
        rows=rows,
    )


def test_detect_probable_duplicates_exact_matches() -> None:
    dataset = build_dataset(
        [
            {
                "transaction_id": "1",
                "date": "2023-10-01T10:00:00Z",
                "amount": 5000.0,
                "status": "posted",
                "type": "debit",
                "counterparty": "Merchant A",
                "source_account_id": "account-1",
                "event_type": "card_payment",
            },
            {
                "transaction_id": "2",
                "date": "2023-10-01T10:00:00Z",
                "amount": 5000.0,
                "status": "posted",
                "type": "debit",
                "counterparty": "Merchant A",
                "source_account_id": "account-1",
                "event_type": "card_payment",
            },
        ]
    )
    result = detect_probable_duplicates(dataset, min_confidence=0.8)
    assert result.available is True
    assert len(result.candidates) == 1
    assert result.candidates[0].redundant_value == Decimal("5000.0")


@pytest.mark.parametrize(
    "row_override, expected_count",
    [
        # Base negative controls
        ({"status": "failed"}, 0),
        ({"status": "pending"}, 0),
        ({"status": "reversed"}, 0),
        # Canonical internal exclusion
        ({"is_internal_transfer": True}, 0),
        ({"cash_flow_class": "internal"}, 0),
        # Canonical fee exclusion
        ({"event_type": "fee"}, 0),
        # Valid repeats vs feed duplicates
        # The same stable source reference is a duplicated observation even if feed timestamps drift.
        ({"date": "2023-10-01T10:05:00Z", "reference": "REF123"}, 1),
        # Same time, same reference -> 1.0 confidence (duplicate feed observation)
        ({"date": "2023-10-01T10:00:00Z", "reference": "REF123"}, 1),
        # Same time, different references -> 0.1 confidence (different logical events)
        ({"date": "2023-10-01T10:00:00Z", "reference": "REF456"}, 0),
    ],
)
def test_probable_duplicates_safety_exclusions(row_override: dict[str, Any], expected_count: int) -> None:
    base_row = {
        "transaction_id": "1",
        "date": "2023-10-01T10:00:00Z",
        "amount": 5000.0,
        "status": "posted",
        "type": "debit",
        "counterparty": "Merchant A",
        "source_account_id": "account-1",
        "reference": "REF123",
        "event_type": "card_payment",
        "cash_flow_class": "operating",
        "is_internal_transfer": False,
    }

    # We always include the base_row, then a second row which might be overridden
    second_row = {**base_row, "transaction_id": "2", **row_override}

    dataset = build_dataset([base_row, second_row])
    result = detect_probable_duplicates(dataset, min_confidence=0.8)

    assert len(result.candidates) == expected_count


def test_detect_probable_duplicates_none_found() -> None:
    dataset = build_dataset(
        [
            {"transaction_id": "1", "date": "2023-10-01T10:00:00Z", "amount": 5000.0, "status": "posted"},
            {"transaction_id": "2", "date": "2023-10-02T10:00:00Z", "amount": 1000.0, "status": "posted"},
        ]
    )
    result = detect_probable_duplicates(dataset, min_confidence=0.8)
    assert len(result.candidates) == 0
    assert result.total_redundant_value == Decimal("0")


def test_reference_free_duplicates_require_same_canonical_fields_and_sixty_second_window() -> None:
    base = {
        "transaction_id": "1",
        "date": "2023-10-01T10:00:00Z",
        "amount": 5000,
        "currency": "NGN",
        "status": "posted",
        "type": "debit",
        "counterparty": "Merchant A",
        "source_account_id": "account-1",
        "event_type": "card_payment",
        "cash_flow_class": "operating",
        "semantic_resolution_state": "resolved",
    }
    within_window = {**base, "transaction_id": "2", "date": "2023-10-01T10:00:45Z"}
    outside_window = {**base, "transaction_id": "3", "date": "2023-10-01T10:02:00Z"}

    result = detect_probable_duplicates(build_dataset([base, within_window, outside_window]))

    assert len(result.candidates) == 1
    assert {row["transaction_id"] for row in result.candidates[0].transactions} == {"1", "2"}


def test_unavailable_coverage_never_exposes_duplicate_candidates() -> None:
    dataset = build_dataset(
        [
            {
                "transaction_id": identifier,
                "date": "2023-10-01T10:00:00Z",
                "amount": 5000,
                "status": "posted",
                "type": "debit",
                "counterparty": "Merchant A",
                "source_account_id": "account-1",
                "event_type": "card_payment",
                "reference": "REF123",
            }
            for identifier in ("1", "2")
        ]
    ).model_copy(update={"coverage_status": CoverageStatus.UNAVAILABLE})

    result = detect_probable_duplicates(dataset)

    assert result.available is False
    assert result.candidates == []
