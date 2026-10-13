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


@pytest.mark.parametrize("status", ["completed", "no_results", "failed", "pending", "rate_limited", "partial_rate_limited"])
def test_valid_outcome_round_trips(candidate, status):
    candidates = [candidate] if status in {"completed", "partial_rate_limited"} else []
    outcome = DiscoveryOutcome(status=status, candidates=candidates)
    assert DiscoveryOutcome.model_validate_json(outcome.model_dump_json()) == outcome


@pytest.mark.parametrize(("status", "has_candidate"), [
    ("completed", False), ("no_results", True), ("pending", True),
    ("rate_limited", True), ("partial_rate_limited", False),
])
def test_incoherent_outcome_is_rejected(candidate, status, has_candidate):
    with pytest.raises(ValidationError):
        DiscoveryOutcome(status=status, candidates=[candidate] if has_candidate else [])


def test_failed_outcome_allows_no_candidates():
    outcome = DiscoveryOutcome(status="failed")
    assert outcome.candidates == []


def test_completed_discovery_can_coexist_with_a_later_phase_error(candidate):
    state = BugFixingState(discovery_outcome=DiscoveryOutcome(status="completed", candidates=[candidate]), workflow_error=WorkflowError(
        phase="triage", category="operational", message="Later failure",
    ))
    assert route_after_discovery(state) == "completed"


def test_router_rejects_completed_state_without_candidates():
    with pytest.raises(ValidationError):
        DiscoveryOutcome(status="completed")


def test_router_routes_failed_state_to_failure_branch():
    state = BugFixingState(discovery_outcome=DiscoveryOutcome(status="failed"))
    assert route_after_discovery(state) == "failed"
