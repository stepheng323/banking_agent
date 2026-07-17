import pytest
from pydantic import ValidationError

from shared.types.planner import AccountTaskParameters, make_planned_task
from shared.types.read import ReadRequest, ReadResult, normalize_read_request


def test_read_request_accepts_filtered_beneficiary_count() -> None:
    request = ReadRequest(subject="beneficiary", response_shape="fact_count", entity_name="Tolu")

    assert request.page_size == 5
    assert request.offset == 0
    assert request.model_dump(mode="json")["entity_name"] == "Tolu"


def test_read_request_rejects_invalid_subject_shape() -> None:
    with pytest.raises(ValidationError):
        ReadRequest(subject="receipt", response_shape="fact_count")


def test_read_result_enforces_page_metadata() -> None:
    request = ReadRequest(subject="beneficiary", response_shape="surface_list", offset=5)
    result = ReadResult(request=request, total_count=8, returned_count=3, has_previous=True)

    assert result.has_previous is True
    assert result.has_next is False


def test_flat_schedule_metadata_is_not_accepted() -> None:
    request = normalize_read_request(
        {"schedule_response_mode": "count"},
    )

    assert request is None


def test_flat_planner_read_metadata_is_rejected_after_cutover() -> None:
    with pytest.raises(ValidationError):
        make_planned_task(
            task_id="q-flat",
            action="transaction_search",
            executor="query",
            instruction="Show transactions",
            parameters={"response_shape": "surface_list"},
            risk="READ_ONLY",
        )


def test_mutation_task_rejects_read_request() -> None:
    with pytest.raises(ValidationError):
        make_planned_task(
            task_id="a1",
            action="set_default",
            executor="account",
            instruction="Set Access as default",
            parameters=AccountTaskParameters(
                read_request=ReadRequest(subject="default_account", response_shape="fact_value")
            ),
            risk="MUTATION",
        )
