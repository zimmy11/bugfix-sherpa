"""Validate runner-to-State conversion and overall investigation conclusions."""

import pytest
from pydantic import ValidationError

from src.agent.schema import SherpaReport, TestExecutionResult as ExecutionResult
from src.agent.state import BugFixingState, test_execution_update as runner_update


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf")])
def test_state_rejects_invalid_overall_confidence(score):
    with pytest.raises(ValidationError):
        BugFixingState(confidence_score=score, conclusion_reason="Supported by evidence.")


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_overall_confidence_accepts_bounds_with_a_reason(score):
    state = BugFixingState(confidence_score=score, conclusion_reason="Supported by evidence.")
    assert state.confidence_score == score


@pytest.mark.parametrize("changes", [{"confidence_score": 0.8}, {"root_cause": "Missing guard"}])
@pytest.mark.parametrize("reason", [None, "", " \t\n"])
def test_state_conclusion_requires_its_own_reason(changes, reason):
    with pytest.raises(ValidationError):
        BugFixingState(**changes, conclusion_reason=reason)


def test_finding_reason_does_not_supply_the_overall_reason(finding_data):
    with pytest.raises(ValidationError, match="motivazione complessiva"):
        BugFixingState(investigation_findings=[finding_data], confidence_score=0.8)


@pytest.mark.parametrize("field", ["current_hypothesis", "root_cause", "test_command"])
def test_state_rejects_blank_intermediate_text(field):
    with pytest.raises(ValidationError):
        BugFixingState(**{field: " \t\n"})


def test_command_can_be_planned_without_claiming_an_execution():
    state = BugFixingState(test_command="python -m pytest")
    assert state.get_test_execution_result() is None
    assert state.tests_passed is None


@pytest.mark.parametrize(
    "changes",
    [
        {"tests_passed": True},
        {"tests_passed": False},
        {"test_exit_code": 0},
        {"test_timeout": True},
        {"test_command": "python -m pytest", "test_logs": "failure"},
        {"test_command": "python -m pytest", "tests_passed": True},
        {"test_command": "python -m pytest", "test_exit_code": True},
        {"test_command": "python -m pytest", "test_exit_code": "0"},
        {"test_command": "python -m pytest", "test_exit_code": 0, "test_timeout": "false"},
    ],
)
def test_state_rejects_incomplete_or_coerced_runner_fields(changes):
    with pytest.raises(ValidationError):
        BugFixingState(**changes)


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "claimed_passed"),
    [(1, False, True), (-9, False, True), (None, True, True), (0, True, True), (0, False, False)],
)
def test_state_rejects_outcome_conflicting_with_runner(exit_code, timed_out, claimed_passed):
    with pytest.raises(ValidationError, match="incoerente con il risultato"):
        BugFixingState(
            test_command="python -m pytest", test_exit_code=exit_code,
            test_timeout=timed_out, tests_passed=claimed_passed,
        )


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "status"),
    [(0, False, "passed"), (1, False, "failed"), (None, True, "timeout"), (0, True, "timeout")],
)
def test_runner_result_round_trips_through_state_and_report(
    report_data, exit_code, timed_out, status,
):
    runner = ExecutionResult(
        command="python -m pytest", exit_code=exit_code, timed_out=timed_out,
        logs="  runner output\n",
    )
    update = runner_update(runner)
    assert update["tests_passed"] == (status == "passed")
    state = BugFixingState(**update)
    converted = state.get_test_execution_result()
    assert converted == runner
    report = SherpaReport(**report_data, test_result=converted, test_status=converted.status)
    state = BugFixingState(**update, report=report)
    restored = BugFixingState.model_validate_json(state.model_dump_json())
    assert restored.get_test_execution_result() == runner
    assert restored.report.test_result.logs == "  runner output\n"


def test_runner_metadata_determines_status_without_a_summary_flag():
    state = BugFixingState(test_command="python -m pytest", test_exit_code=0)
    assert state.get_test_execution_result().status == "passed"


@pytest.mark.parametrize("runner_code", [None, 0, 1])
def test_state_rejects_report_result_that_did_not_come_from_its_runner(report_data, runner_code):
    report = SherpaReport(
        **report_data, test_status="passed",
        test_result=ExecutionResult(command="python -m pytest", exit_code=0, logs="actual output"),
    )
    changes = {} if runner_code is None else {
        "test_command": "python -m pytest", "test_exit_code": runner_code,
        "test_logs": "different output",
    }
    with pytest.raises(ValidationError, match="report deve usare il risultato"):
        BugFixingState(**changes, report=report)


def test_report_cannot_omit_an_execution_present_in_state(report_data):
    with pytest.raises(ValidationError, match="report deve usare il risultato"):
        BugFixingState(
            test_command="python -m pytest", test_exit_code=1,
            report=SherpaReport(**report_data),
        )


@pytest.mark.parametrize("result", [
    {"command": "python -m pytest"},
    {"command": " ", "exit_code": 0},
    {"command": "python -m pytest", "exit_code": True},
    {"command": "python -m pytest", "exit_code": 0, "timed_out": "false"},
])
def test_execution_result_rejects_untrustworthy_metadata(result):
    with pytest.raises(ValidationError):
        ExecutionResult(**result)
