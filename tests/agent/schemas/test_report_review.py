"""Review decisions are validated against the exact current report."""

import pytest
from pydantic import ValidationError

from src.agent.schema import SherpaReport
from src.agent.state import BugFixingState, report_review_update, report_update


@pytest.mark.parametrize("decision", ["approved", "revision_requested", "rejected"])
def test_review_round_trips_with_current_report(report_data, decision):
    state = BugFixingState(report=SherpaReport(**report_data))
    update = report_review_update(state, decision, "Please check the reproducer")
    reviewed = BugFixingState.model_validate({**state.model_dump(), **update})
    restored = BugFixingState.model_validate_json(reviewed.model_dump_json())
    assert restored.reviewed_report_id == restored.report.report_id
    assert restored.approval_status == decision


@pytest.mark.parametrize("decision", ["approved", "revision_requested", "rejected"])
def test_decision_without_report_is_rejected(decision):
    with pytest.raises(ValidationError):
        BugFixingState(approval_status=decision)


@pytest.mark.parametrize("decision", ["revision_requested", "rejected"])
@pytest.mark.parametrize("feedback", [None, "", " \t\n"])
def test_negative_decision_requires_feedback(report_data, decision, feedback):
    state = BugFixingState(report=SherpaReport(**report_data))
    with pytest.raises(ValidationError):
        report_review_update(state, decision, feedback)


def test_approval_can_omit_feedback(report_data):
    state = BugFixingState(report=SherpaReport(**report_data))
    assert report_review_update(state, "approved")["human_feedback"] is None


def test_updated_report_cannot_keep_old_approval(report_data):
    report = SherpaReport(**report_data)
    state = BugFixingState(report=report)
    reviewed = BugFixingState.model_validate({**state.model_dump(), **report_review_update(state, "approved")})
    replacement = SherpaReport(**{**report_data, "problem_summary": "Updated analysis"})
    with pytest.raises(ValidationError, match="report corrente"):
        BugFixingState.model_validate({**reviewed.model_dump(), "report": replacement})
    updated = BugFixingState.model_validate({**reviewed.model_dump(), **report_update(reviewed, replacement)})
    assert updated.approval_status == "pending"
    assert updated.reviewed_report_id is None
    assert updated.human_feedback is None


def test_pending_state_needs_no_report_or_feedback():
    assert BugFixingState().approval_status == "pending"


def test_feedback_without_a_decision_is_rejected(report_data):
    report = SherpaReport(**report_data)
    with pytest.raises(ValidationError):
        BugFixingState(report=report, reviewed_report_id=report.report_id, human_feedback="Review")


def test_report_fingerprint_is_stable_across_json(report_data):
    report = SherpaReport(**report_data)
    assert SherpaReport.model_validate_json(report.model_dump_json()).report_id == report.report_id
