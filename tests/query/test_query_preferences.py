from datetime import date

import pytest

from banking.runtime.operations import operation_spec
from banking.runtime.results import TransactionOutcome
from banking.transactions.query.models.domain import QueryIntent
from banking.transactions.query.models.extraction import (
    QueryExtractionResult,
    QueryParseResult,
    QueryTimeRange,
    ResolverOutcome,
    TimeReference,
)
from banking.transactions.query.nodes.extraction import ExtractionStep
from banking.transactions.query.preferences import (
    QueryPreferenceAccountError,
    apply_query_preference_update,
)
from banking.transactions.query.worker import QueryWorker
from shared.types.planner import QueryPlannedTask
from shared.types.query_preferences import (
    QueryPreferencesV1,
    QueryPreferenceUpdate,
    query_preferences_from_profile,
)


class _NoCallStructured:
    async def ainvoke(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("the preference path must not invoke an LLM")


class _NoCallLLM:
    def with_structured_output(self, schema: object, **kwargs: object) -> _NoCallStructured:
        del schema, kwargs
        return _NoCallStructured()


def test_query_preference_update_resolves_stable_account_reference() -> None:
    updated = apply_query_preference_update(
        QueryPreferencesV1(presentation_detail="concise"),
        QueryPreferenceUpdate(
            presentation_detail="detailed",
            default_account_names=["GTBank"],
            default_status_inclusion="settled",
        ),
        accounts=[{"id": "linked-1", "account_id": "provider-1", "bank_name": "GTBank"}],
    )

    assert updated.presentation_detail == "detailed"
    assert updated.default_status_inclusion == "settled"
    assert updated.default_account_refs == ["provider-1"]


def test_query_preference_account_scope_requires_one_exact_linked_account() -> None:
    with pytest.raises(QueryPreferenceAccountError):
        apply_query_preference_update(
            QueryPreferencesV1(),
            QueryPreferenceUpdate(default_account_names=["Access"]),
            accounts=[
                {"account_id": "one", "bank_name": "Access"},
                {"account_id": "two", "bank_name": "Access"},
            ],
        )


def test_invalid_profile_preferences_are_ignored_safely() -> None:
    preferences = query_preferences_from_profile(
        {"extra_data": {"query_preferences": {"schema_version": 99, "presentation_detail": "verbose"}}}
    )

    assert preferences == QueryPreferencesV1()


def test_compiled_query_applies_explicit_default_account_and_detail_preferences() -> None:
    step = ExtractionStep(_NoCallLLM())
    result = QueryParseResult(
        outcome=ResolverOutcome.OK,
        extraction=QueryExtractionResult(
            intent=QueryIntent.ANALYTICS_SUMMARY,
            time_range=QueryTimeRange(reference_type=TimeReference.EXPLICIT, period="this_month"),
            use_default_account_scope=True,
        ),
    )

    updates = step._parse_result_to_updates(
        result,
        state={
            "message": "How am I doing this month?",
            "query_preferences": {
                "presentation_detail": "detailed",
                "default_account_refs": ["provider-1", "provider-2"],
            },
        },
        today=date(2026, 7, 29),
        language="en",
    )

    assert updates["show_expanded"] is True
    assert updates["account_ids"] == ["provider-1", "provider-2"]


@pytest.mark.asyncio
async def test_query_worker_preference_mutation_is_zero_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _persist(**kwargs: object) -> QueryPreferencesV1:
        assert kwargs["user_id"] == "user-1"
        return QueryPreferencesV1(presentation_detail="detailed")

    monkeypatch.setattr("banking.transactions.query.worker.persist_query_preferences", _persist)
    worker = QueryWorker(_NoCallLLM(), object())  # type: ignore[arg-type]
    result = await worker.run(
        payload={
            "action": "update_query_preferences",
            "preferences_update": {"presentation_detail": "detailed"},
        },
        context={
            "user_id": "user-1",
            "phone_number": "2348000000000",
            "profile": {},
            "accounts": [],
            "language": "en",
        },
    )

    assert result.outcome == TransactionOutcome.OK
    assert result.patch == {
        "query_preferences": QueryPreferencesV1(presentation_detail="detailed").model_dump(mode="json")
    }


def test_query_preference_operation_is_a_no_confirmation_mutation() -> None:
    operation = operation_spec("query", "update_query_preferences")
    task = QueryPlannedTask(
        task_id="preferences",
        action="update_query_preferences",
        instruction="Always show detailed transaction answers",
        risk="MUTATION",
        parameters={"preferences_update": {"presentation_detail": "detailed"}},
    )

    assert task.parameters.preferences_update.presentation_detail == "detailed"
    assert operation.risk == "MUTATION"
    assert operation.requires_confirmation is False
    assert operation.requires_pin is False
