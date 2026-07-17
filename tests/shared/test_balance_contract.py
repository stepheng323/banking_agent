from shared.types.balance import (
    BalanceConversationState,
    BalanceFollowupDelta,
    BalanceQueryContract,
    apply_balance_followup,
)


def test_mentioned_scope_supports_more_than_two_accounts() -> None:
    contract = BalanceQueryContract(
        account_scope="named",
        bank_names=["GTBank"],
        operation="value",
    )
    state = BalanceConversationState(
        focused_bank="GTBank",
        mentioned_banks=["Access", "GTB", "First Bank", "Zenith"],
        last_result_banks=["GTBank"],
        last_operation="value",
    )

    resolved = apply_balance_followup(
        contract,
        state,
        BalanceFollowupDelta(scope_operation="mentioned", operation="compare"),
    )

    assert resolved is not None
    assert resolved.bank_names == ["Access Bank", "GTBank", "First Bank", "Zenith Bank"]
    assert resolved.operation == "compare"


def test_unresolved_contextual_scope_does_not_guess() -> None:
    contract = BalanceQueryContract(account_scope="named", bank_names=["GTBank"], operation="value")
    state = BalanceConversationState(mentioned_banks=["GTBank"], last_result_banks=["GTBank"])

    resolved = apply_balance_followup(
        contract,
        state,
        BalanceFollowupDelta(scope_operation="mentioned", operation="total"),
    )

    assert resolved is None


def test_recent_two_is_distinct_from_the_full_mentioned_set() -> None:
    contract = BalanceQueryContract(account_scope="named", bank_names=["First Bank"], operation="value")
    state = BalanceConversationState(
        mentioned_banks=["Access Bank", "GTBank", "First Bank"],
        last_result_banks=["First Bank"],
    )

    resolved = apply_balance_followup(
        contract,
        state,
        BalanceFollowupDelta(scope_operation="recent_two", operation="total"),
    )

    assert resolved is not None
    assert resolved.bank_names == ["GTBank", "First Bank"]


def test_add_and_remove_are_deterministic_set_operations() -> None:
    contract = BalanceQueryContract(
        account_scope="named",
        bank_names=["Access Bank", "GTBank", "First Bank"],
        operation="breakdown",
    )
    state = BalanceConversationState()

    added = apply_balance_followup(
        contract,
        state,
        BalanceFollowupDelta(scope_operation="add", bank_names=["Zenith"], operation="compare"),
    )
    assert added is not None
    assert added.bank_names == ["Access Bank", "GTBank", "First Bank", "Zenith Bank"]

    removed = apply_balance_followup(
        added,
        state,
        BalanceFollowupDelta(scope_operation="remove", bank_names=["GTB", "Access"]),
    )
    assert removed is not None
    assert removed.bank_names == ["First Bank", "Zenith Bank"]
