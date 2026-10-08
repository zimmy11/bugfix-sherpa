"""Workflow error contract, safe diagnostics and State serialization.

All credentials used below are synthetic test markers.
"""

import pytest
from pydantic import ValidationError

from src.agent.schema import WorkflowError
from src.agent.state import BugFixingState


@pytest.mark.parametrize("phase", [
    "discovery", "triage", "ingestion", "investigation", "advisory", "human_review",
])
def test_error_round_trips_through_state(phase):
    error = WorkflowError(phase=phase, category="operational", message="Temporary failure", retryable=True)
    state = BugFixingState(workflow_error=error)
    restored = BugFixingState.model_validate_json(state.model_dump_json())
    assert restored.workflow_error == error


@pytest.mark.parametrize("category", ["invalid_input", "invariant_violation"])
def test_non_operational_errors_cannot_be_retryable(category):
    with pytest.raises(ValidationError):
        WorkflowError(phase="discovery", category=category, message="Invalid data", retryable=True)


@pytest.mark.parametrize("category", ["operational", "invalid_input", "invariant_violation"])
def test_non_retryable_error_categories_are_valid(category):
    error = WorkflowError(phase="triage", category=category, message="Cannot continue")
    assert error.retryable is False


@pytest.mark.parametrize("changes", [
    {"phase": "unknown"}, {"category": "unknown"},
    {"message": None}, {"message": ""}, {"message": " \t\n"},
    {"retryable": "false"}, {"retryable": 1},
])
def test_error_rejects_invalid_contract_fields(changes):
    data = {"phase": "discovery", "category": "operational", "message": "Failure"}
    data.update(changes)
    with pytest.raises(ValidationError):
        WorkflowError.model_validate(data)


@pytest.mark.parametrize("field", ["phase", "category", "message"])
def test_error_requires_all_identifying_fields(field):
    data = {"phase": "discovery", "category": "operational", "message": "Failure"}
    data.pop(field)
    with pytest.raises(ValidationError):
        WorkflowError.model_validate(data)


@pytest.mark.parametrize("message", [
    "GitHub failure: token=TEST_ONLY_SECRET",
    "Request failed: Authorization: Bearer TEST_ONLY_SECRET",
    "Clone failed: https://user:TEST_ONLY_SECRET@github.com/owner/project.git",
    "Model failure: api_key=TEST_ONLY_SECRET",
])
def test_sensitive_diagnostics_are_sanitized_before_storage(message):
    error = WorkflowError(phase="discovery", category="operational", message=message)
    assert "TEST_ONLY_SECRET" not in error.message
    assert "TEST_ONLY_SECRET" not in error.model_dump_json()
    assert error.message.strip()


def test_safe_diagnostic_is_preserved():
    error = WorkflowError(phase="triage", category="invariant_violation", message="Selected issue is absent")
    assert error.message == "Selected issue is absent"



@pytest.mark.parametrize("message", [
    'Failure: {"api_key": "TEST_ONLY_SECRET with spaces"}',
    "Failure: password='TEST_ONLY_SECRET with spaces'",
    "Failure: GITHUB_TOKEN=TEST_ONLY_SECRET",
    "Failure: https://user:TEST_ONLY_SECRET@part@github.com/owner/project.git",
    "Failure: https://github.com/owner/project?credential=TEST_ONLY_SECRET#details",
    "Failure: Authorization: Basic TEST_ONLY_SECRET",
    "Failure: github_pat_TEST_ONLY_SECRET",
])
def test_additional_credential_formats_are_removed(message):
    error = WorkflowError(phase="triage", category="operational", message=message)
    assert "TEST_ONLY_SECRET" not in error.message
    state = BugFixingState(workflow_error=error)
    assert "TEST_ONLY_SECRET" not in state.model_dump_json()
    assert WorkflowError.model_validate_json(error.model_dump_json()) == error


def test_message_assignment_is_sanitized_too():
    error = WorkflowError(phase="discovery", category="operational", message="Safe failure")
    error.message = "Failure: token=TEST_ONLY_SECRET"
    assert "TEST_ONLY_SECRET" not in error.message
