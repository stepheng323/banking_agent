from __future__ import annotations

from datetime import date

from scripts.seed_user_test_data import _variance_demo_transaction_specs


def test_variance_demo_seed_has_current_and_baseline_acceptance_coverage() -> None:
    specs = _variance_demo_transaction_specs(date(2026, 7, 26))
    identifiers = {str(spec["provider_transaction_id"]) for spec in specs}

    assert len(specs) == 16
    assert {"variance-demo-current-food", "variance-demo-baseline-food"} <= identifiers
    assert {"variance-demo-current-salary", "variance-demo-baseline-salary"} <= identifiers
    assert {"variance-demo-current-investment", "variance-demo-baseline-investment"} <= identifiers
    assert {"variance-demo-current-internal", "variance-demo-baseline-internal"} <= identifiers
    assert {"variance-demo-current-unresolved", "variance-demo-baseline-unresolved"} <= identifiers

    current_food = next(spec for spec in specs if spec["provider_transaction_id"] == "variance-demo-current-food")
    baseline_food = next(spec for spec in specs if spec["provider_transaction_id"] == "variance-demo-baseline-food")
    assert current_food["amount"] == 60_000
    assert baseline_food["amount"] == 20_000
