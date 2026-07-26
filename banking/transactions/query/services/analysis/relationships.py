"""Relationship detection candidate builders."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal
from typing import Any

from banking.transactions.query.services.analysis.kernel.contracts import (
    AnalysisDataset,
    AnalysisMetric,
    DuplicateCandidateGroup,
    ProbableDuplicatesResult,
)
from banking.transactions.query.services.fetching.fetch import parse_date


def detect_probable_duplicates(
    dataset: AnalysisDataset,
    min_confidence: float = 0.8,
) -> ProbableDuplicatesResult:
    """Detect probable duplicate transactions based on identical characteristics."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dataset.rows:
        date_str = str(row.get("date") or "")
        dt = parse_date(date_str)
        if dt is None:
            continue
            
        amount = abs(float(row.get("amount") or 0))
        if amount == 0:
            continue
            
        direction = str(row.get("direction") or "unknown").lower()
        counterparty = str(row.get("counterparty") or row.get("narration") or "unknown").strip().lower()
        
        # We group by exact date, amount, and direction
        # Counterparty can be slightly fuzzy, but exact match is high confidence
        key = f"{dt.isoformat()}|{amount:.2f}|{direction}|{counterparty}"
        groups[key].append(row)
        
    candidates: list[DuplicateCandidateGroup] = []
    total_redundant = Decimal("0")
    for key, txs in groups.items():
        if len(txs) > 1:
            amount = abs(float(txs[0].get("amount") or 0))
            direction = str(txs[0].get("direction") or "unknown").lower()
            metric = AnalysisMetric.INCOME if direction == "credit" else AnalysisMetric.SPENDING
            
            group_id = hashlib.md5(key.encode("utf-8")).hexdigest()
            redundant_val = Decimal(str(amount)) * (len(txs) - 1)
            
            candidates.append(
                DuplicateCandidateGroup(
                    group_id=group_id,
                    confidence=0.95, # Exact match on key fields
                    transactions=txs,
                    redundant_value=redundant_val,
                    metric=metric,
                )
            )
            
    # Filter by confidence
    candidates = [c for c in candidates if c.confidence >= min_confidence]
    # Sort by redundant amount descending
    candidates.sort(key=lambda c: c.redundant_value, reverse=True)
    
    for c in candidates:
        total_redundant += c.redundant_value

    return ProbableDuplicatesResult(
        basis=dataset.basis,
        dataset=dataset,
        candidates=candidates,
        total_redundant_value=total_redundant,
        coverage=dataset.coverage_status,
        available=dataset.coverage_status.value != "unavailable",
        unavailable_reason="coverage_incomplete" if dataset.coverage_status.value == "unavailable" else None,
    )
