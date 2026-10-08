"""Final Discovery outcome coherence and routing of assembled State."""

import pytest
from pydantic import ValidationError

from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError
from src.agent.state import BugFixingState
from src.agent.router import route_after_discovery


@pytest.fixture
def candidate():
    return IssueCandidate(
        repository_full_name="owner/project", issue_number=42, title="Bug",
        url="https://github.com/owner/project/issues/42", updated_at="2026-01-01",
        repository_stats={"stars": 1, "forks": 0, "open_issues": 1,
                          "default_branch": "main", "last_pushed_at": "2026-01-01", "has_issues": True},
    )


@pytest.mark.parametrize("status", ["completed", "no_results", "failed"])
def test_valid_outcome_round_trips(candidate, status):
    error = WorkflowError(phase="discovery", category="operational", message="Quota", retryable=True) if status == "failed" else None
    candidates = [candidate] if status != "no_results" else []
    outcome = DiscoveryOutcome(status=status, candidates=candidates, error=error)
    assert DiscoveryOutcome.model_validate_json(outcome.model_dump_json()) == outcome


@pytest.mark.parametrize(("status", "has_candidate", "error_phase"), [
    ("completed", False, None), ("no_results", True, None),
    ("completed", True, "discovery"), ("no_results", False, "discovery"),
    ("failed", False, None), ("failed", True, "triage"), ("pending", False, None),
])
def test_incoherent_outcome_is_rejected(candidate, status, has_candidate, error_phase):
    error = WorkflowError(phase=error_phase, category="operational", message="Failure") if error_phase else None
    with pytest.raises(ValidationError):
        DiscoveryOutcome(status=status, candidates=[candidate] if has_candidate else [], error=error)


def test_failed_outcome_allows_no_candidates():
    outcome = DiscoveryOutcome(status="failed", error=WorkflowError(
        phase="discovery", category="invariant_violation", message="Invalid result",
    ))
    assert outcome.candidates == []


def test_completed_discovery_can_coexist_with_a_later_phase_error(candidate):
    state = BugFixingState(discovery_status="completed", issue_candidates=[candidate], workflow_error=WorkflowError(
        phase="triage", category="operational", message="Later failure",
    ))
    assert route_after_discovery(state) == "completed"


def test_router_rejects_completed_state_without_candidates():
    with pytest.raises(ValidationError):
        route_after_discovery(BugFixingState(discovery_status="completed"))


def test_router_rejects_failed_state_without_discovery_error():
    with pytest.raises(ValidationError):
        route_after_discovery(BugFixingState(discovery_status="failed"))
