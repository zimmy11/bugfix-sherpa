"""Input and counter bounds for the workflow state."""

import pytest
from pydantic import ValidationError

from src.agent.state import BugFixingState


@pytest.mark.parametrize("field", ("ingestion_attempts", "retry_count"))
def test_counters_start_at_zero(field):
    assert getattr(BugFixingState(), field) == 0


@pytest.mark.parametrize("field", ("ingestion_attempts", "retry_count"))
@pytest.mark.parametrize("value", (-1, -2))
def test_negative_counters_are_rejected(field, value):
    with pytest.raises(ValidationError) as exc_info:
        BugFixingState(**{field: value})

    assert any(error["loc"] == (field,) for error in exc_info.value.errors())


@pytest.mark.parametrize("value", (1, 100))
def test_max_results_accepts_boundaries(value):
    assert BugFixingState(max_results=value).max_results == value


@pytest.mark.parametrize("value", (0, -1, 101))
def test_max_results_rejects_out_of_range(value):
    with pytest.raises(ValidationError) as exc_info:
        BugFixingState(max_results=value)

    assert any(error["loc"] == ("max_results",) for error in exc_info.value.errors())


def test_initial_state_has_valid_result_limit():
    assert 1 <= BugFixingState().max_results <= 100
