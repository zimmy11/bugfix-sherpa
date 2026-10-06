"""Tests for transitions between the agent's workflow phases."""

import pytest

from src.agent.router import route_after_ingestion, route_after_triage
from src.agent.schema import IssueCandidate, RepositoryStats
from src.agent.state import BugFixingState


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


def test_ingestion_rejects_completed_snapshot_without_sha() -> None:
    state = _accepted_issue_state()
    state.ingestion_status = "completed"
    state.local_repo_path = "C:/workspace/owner/project"
    state.repository_remote_url = "https://github.com/owner/project.git"
    state.test_config_files = []
    state.repository_revision = None

    with pytest.raises(ValueError, match="SHA del checkout mancante"):
        route_after_ingestion(state)
