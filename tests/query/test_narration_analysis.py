from apps.core.src.agent.graphs.query.services.narration import analyze_transaction_narration


def test_transfer_narration_extracts_recipient_and_transfer_category() -> None:
    analysis = analyze_transaction_narration(
        narration="0000132312091322123456789012345 NIP TRANSFER TO ADEBAYO JAMES",
        transaction_type="debit",
    )

    assert analysis.counterparty == "Adebayo James"
    assert analysis.counterparty_role == "recipient"
    assert analysis.resolved_category == "transfers"
    assert analysis.parser_rule == "nip_transfer"


def test_salary_narration_extracts_employer_and_income_category() -> None:
    analysis = analyze_transaction_narration(
        narration="Salary from Acme Corp",
        transaction_type="credit",
    )

    assert analysis.counterparty == "Acme Corp"
    assert analysis.counterparty_role == "employer"
    assert analysis.resolved_category == "income"
    assert analysis.parser_rule == "salary"


def test_subscription_narration_extracts_merchant_and_entertainment_category() -> None:
    analysis = analyze_transaction_narration(
        narration="Netflix Monthly Subscription",
        transaction_type="debit",
    )

    assert analysis.counterparty == "Netflix"
    assert analysis.counterparty_role == "merchant"
    assert analysis.resolved_category == "entertainment"


def test_provider_counterparty_is_used_when_narration_parser_has_no_match() -> None:
    analysis = analyze_transaction_narration(
        narration="VALUE DATE ADJUSTMENT",
        transaction_type="credit",
        provider_counterparty="Acme Settlements",
        provider_category="income",
    )

    assert analysis.counterparty == "Acme Settlements"
    assert analysis.counterparty_source == "provider"
    assert analysis.resolved_category == "income"
