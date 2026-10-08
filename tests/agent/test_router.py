"""Tests for transitions between the agent's workflow phases."""

import pytest
from langchain_core.messages import AIMessage

from src.agent.router import route_after_discovery, route_after_ingestion, route_after_triage
from src.agent.schema import IssueCandidate, RepositoryStats, WorkflowError
from src.agent.state import BugFixingState
from src.utils.config import Settings


def _accepted_issue_state() -> BugFixingState:
    candidate = IssueCandidate(
        repository_full_name="owner/project",
        issue_number=42,
        title="Example bug",
        url="https://github.com/owner/project/issues/42",
        updated_at="2026-01-01T00:00:00+00:00",
        repository_stats=RepositoryStats(
            stars=100,
            forks=10,
            open_issues=5,
            default_branch="main",
            last_pushed_at="2026-01-01T00:00:00+00:00",
            has_issues=True,
        ),
    )
    return BugFixingState(
        issue_candidates=[candidate],
        selected_issue=candidate,
        issue_number=candidate.issue_number,
        issue_url=candidate.url,
        repository_full_name=candidate.repository_full_name,
        default_branch=candidate.repository_stats.default_branch,
        triage_status="accepted",
    )


def test_triage_rejects_url_different_from_candidate() -> None:
    state = _accepted_issue_state()
    state.issue_url = "https://github.com/owner/project/issues/99"

    with pytest.raises(ValueError, match="URL della issue incoerente"):
        route_after_triage(state)


def test_ingestion_rejects_completed_snapshot_without_sha(tmp_path) -> None:
    state = _accepted_issue_state()
    state.ingestion_status = "completed"
    state.inspection_status = "completed"
    state.local_repo_path = "C:/workspace/owner/project"
    state.repository_remote_url = "https://github.com/owner/project.git"
    state.test_config_files = []
    state.repository_revision = None
    settings = Settings(
        github_token="fake",
        google_api_key="fake",
        workspace_root=tmp_path,
    )

    with pytest.raises(ValueError, match="SHA del checkout mancante"):
        route_after_ingestion(state, settings=settings)



@pytest.mark.parametrize("with_tool_call", [False, True])
@pytest.mark.parametrize("phase", ["discovery", "triage", "ingestion"])
def test_failed_phase_stops_before_stale_tool_calls(phase, with_tool_call):
    error = WorkflowError(phase=phase, category="invariant_violation", message="Invalid transition")
    messages = [AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "stale-call",
    }])] if with_tool_call else []
    state = BugFixingState(**{f"{phase}_status": "failed"}, messages=messages, workflow_error=error)
    if phase == "ingestion":
        outcome = route_after_ingestion(state, settings=Settings(github_token="fake", google_api_key="fake"))
    else:
        router = route_after_discovery if phase == "discovery" else route_after_triage
        outcome = router(state)
    assert outcome == "failed"
    assert state.workflow_error == error


def test_discovery_without_an_explicit_outcome_cannot_be_completed():
    with pytest.raises(ValueError):
        route_after_discovery(BugFixingState())


@pytest.mark.parametrize("phase", ["discovery", "triage"])
def test_pending_phase_with_tool_call_can_still_use_tools(phase):
    state = BugFixingState(messages=[AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "active-call",
    }])])
    router = route_after_discovery if phase == "discovery" else route_after_triage
    assert router(state) == "tools"
