"""Report conclusions and honest handling of unexecuted tests."""

import pytest
from pydantic import ValidationError

from src.agent.schema import SherpaReport


def test_inconclusive_report_is_valid_without_a_root_cause(report_data):
    report = SherpaReport.model_validate(report_data)

    assert report.investigation_status == "inconclusive"
    assert report.main_hypothesis is None
    assert report.findings == []
    assert report.investigation_limits


def test_report_defaults_to_tests_not_run(report_data):
    report = SherpaReport.model_validate(report_data)

    assert report.test_status == "not_run"
    assert report.verified_reproduction_steps == []


def test_conclusive_report_requires_a_hypothesis(report_data, finding_data):
    report_data.update(investigation_status="conclusive", findings=[finding_data])

    with pytest.raises(ValidationError, match="richiede un'ipotesi principale"):
        SherpaReport.model_validate(report_data)


def test_conclusive_report_requires_findings(report_data):
    report_data.update(
        investigation_status="conclusive",
        main_hypothesis="The empty input is not checked before indexing.",
    )

    with pytest.raises(ValidationError, match="richiede evidenze"):
        SherpaReport.model_validate(report_data)


def test_passed_requires_execution_evidence(report_data):
    # A summary flag alone must not certify that tests were executed.
    report_data["test_status"] = "passed"

    with pytest.raises(ValidationError, match="richiede un risultato del runner"):
        SherpaReport.model_validate(report_data)


@pytest.mark.parametrize("status", ["failed", "timeout"])
def test_other_execution_statuses_require_evidence(report_data, status):
    report_data["test_status"] = status

    with pytest.raises(ValidationError, match="richiede un risultato del runner"):
        SherpaReport.model_validate(report_data)


@pytest.mark.parametrize(
    ("status", "exit_code", "timed_out"),
    [("passed", 0, False), ("failed", 1, False), ("timeout", None, True)],
)
def test_report_accepts_matching_runner_result(
    report_data, status, exit_code, timed_out,
):
    report_data.update(
        test_status=status,
        test_result={
            "command": "python -m pytest tests/test_example.py",
            "exit_code": exit_code,
            "timed_out": timed_out,
        },
    )

    report = SherpaReport.model_validate(report_data)
    restored = SherpaReport.model_validate_json(report.model_dump_json())

    assert restored == report
    assert report.test_status == status


@pytest.mark.parametrize(
    ("status", "exit_code", "timed_out"),
    [
        ("passed", 1, False),
        ("passed", 0, True),
        ("failed", 0, False),
        ("failed", None, True),
        ("timeout", 0, False),
    ],
)
def test_report_rejects_status_conflicting_with_runner_result(
    report_data, status, exit_code, timed_out,
):
    report_data.update(
        test_status=status,
        test_result={
            "command": "python -m pytest tests/test_example.py",
            "exit_code": exit_code,
            "timed_out": timed_out,
        },
    )

    with pytest.raises(ValidationError, match="incoerente con il risultato"):
        SherpaReport.model_validate(report_data)


def test_not_run_cannot_have_an_execution_result(report_data):
    report_data["test_result"] = {"command": "python -m pytest", "exit_code": 0}

    with pytest.raises(ValidationError, match="not_run non può contenere"):
        SherpaReport.model_validate(report_data)


@pytest.mark.parametrize(
    "result",
    [{"command": "python -m pytest"}, {"command": "   ", "exit_code": 0}],
)
def test_passed_rejects_incomplete_execution_result(report_data, result):
    report_data.update(test_status="passed", test_result=result)

    with pytest.raises(ValidationError):
        SherpaReport.model_validate(report_data)
