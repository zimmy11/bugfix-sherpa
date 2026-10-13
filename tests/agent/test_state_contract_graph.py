"""Small offline graph proving State model handoff and typed routing."""

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from src.agent.router import route_after_discovery, route_after_triage
from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError
from src.agent.state import BugFixingState, build_initial_state
from src.utils.config import Settings


@pytest.mark.parametrize("triage_fails", (False, True))
def test_offline_graph_passes_models_and_stops_on_typed_error(triage_fails):
    visited = []
    candidate = IssueCandidate(
        repository_full_name="owner/project", issue_number=42, title="Bug",
        url="https://github.com/owner/project/issues/42",
        updated_at="2026-01-01T00:00:00+00:00",
        repository_stats={
            "stars": 100, "forks": 1, "open_issues": 1,
            "default_branch": "main", "last_pushed_at": "2026-01-01T00:00:00+00:00",
            "has_issues": True,
        },
    )

    def discovery(state):
        visited.append("discovery")
        assert state.messages[0].content == "Find issues"
        return {"discovery_outcome": DiscoveryOutcome(status="completed", candidates=[candidate])}

    def triage(state):
        visited.append("triage")
        assert isinstance(state.discovery_outcome.candidates[0], IssueCandidate)
        if triage_fails:
            return {
                "triage_status": "failed",
                "workflow_error": WorkflowError(
                    phase="triage", category="invalid_input",
                    message="Candidate rejected",
                ),
            }
        return {
            "triage_status": "accepted", "selected_issue": state.discovery_outcome.candidates[0],
            "repository_full_name": candidate.repository_full_name,
            "issue_number": candidate.issue_number, "issue_url": candidate.url,
            "default_branch": candidate.repository_stats.default_branch,
        }

    def after_triage(state):
        visited.append("after_triage")
        assert isinstance(state.selected_issue, IssueCandidate)
        return {"status": "ready_for_ingestion"}

    graph = StateGraph(BugFixingState)
    graph.add_node("discovery", discovery)
    graph.add_node("triage", triage)
    graph.add_node("after_triage", after_triage)
    graph.add_edge(START, "discovery")
    graph.add_conditional_edges(
        "discovery", route_after_discovery, {"completed": "triage", "no_results": END, "failed": END},
    )
    graph.add_conditional_edges(
        "triage", route_after_triage,
        {"accepted": "after_triage", "rejected": END, "failed": END, "tools": END},
    )
    graph.add_edge("after_triage", END)
    initial = build_initial_state(
        [HumanMessage(content="  Find   issues  ")],
        Settings(github_token="fake", google_api_key="fake"),
    )

    result = graph.compile().invoke(initial)

    if triage_fails:
        assert visited == ["discovery", "triage"]
        assert result["workflow_error"].category == "invalid_input"
        assert result["status"] == "initialized"
    else:
        assert visited == ["discovery", "triage", "after_triage"]
        assert result["selected_issue"] == candidate
        assert result["status"] == "ready_for_ingestion"
