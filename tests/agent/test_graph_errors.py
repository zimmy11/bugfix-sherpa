"""Offline integration checks of the actual graph's failure branches."""

from types import SimpleNamespace
from unittest.mock import mock_open

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables.graph import Graph

import src.agent.graph as graph_module
from src.agent.schema import IssueCandidate, WorkflowError
from src.agent.state import BugFixingState
from src.utils.config import Settings


class FakeLLM:
    def bind_tools(self, tools):
        return self


@pytest.mark.parametrize("category", ["invalid_input", "invariant_violation"])
def test_triage_failure_ends_graph_without_ingestion_or_retry(monkeypatch, category):
    visited = []
    error = WorkflowError(phase="triage", category=category, message="Cannot continue")

    def discovery(state, **kwargs):
        visited.append("discovery")
        return {"messages": [AIMessage(content="Search finished")], "discovery_status": "completed"}

    def triage(state, **kwargs):
        visited.append("triage")
        return {"triage_status": "failed", "workflow_error": error}

    def forbidden(state, **kwargs):
        pytest.fail("A failed Triage must not reach another phase")

    monkeypatch.setattr(graph_module, "discovery", discovery)
    monkeypatch.setattr(graph_module, "triage", triage)
    monkeypatch.setattr(graph_module, "ingestion", forbidden)
    monkeypatch.setattr(graph_module, "advisor", forbidden)
    monkeypatch.setattr(graph_module, "investigation", forbidden)
    monkeypatch.setattr(graph_module, "human_review", forbidden)
    monkeypatch.setattr(graph_module, "build_github_tools", lambda *args, **kwargs: {})
    monkeypatch.setattr(graph_module, "build_repository_tools", lambda *args, **kwargs: {})
    # Suppress Mermaid network access and graph.png writes during construction.
    monkeypatch.setattr(Graph, "draw_mermaid_png", lambda self, **kwargs: b"")
    monkeypatch.setattr(graph_module, "open", mock_open(), raising=False)

    agent = graph_module.SherpaAgent.__new__(graph_module.SherpaAgent)
    agent.settings = Settings(github_token="fake", google_api_key="fake")
    agent.github_client = object()
    agent.tools = {name: [] for name in ["discovery", "triage", "ingestion"]}
    agent.deps = SimpleNamespace(**{
        name: FakeLLM() for name in ["discovery", "triage", "ingestion", "advisory", "investigation"]
    })
    agent.create_graph()
    candidate = IssueCandidate(
        repository_full_name="owner/project", issue_number=42, title="Bug",
        url="https://github.com/owner/project/issues/42", updated_at="2026-01-01",
        repository_stats={
            "stars": 100, "forks": 1, "open_issues": 1, "default_branch": "main",
            "last_pushed_at": "2026-01-01", "has_issues": True,
        },
    )
    result = agent._graph.invoke(
        BugFixingState(issue_candidates=[candidate]), config={"recursion_limit": 5},
    )
    assert visited == ["discovery", "triage"]
    assert result["triage_status"] == "failed"
    assert result["workflow_error"] == error



def test_failed_tool_stops_actual_graph_before_another_model_call(monkeypatch):
    from langchain_core.tools import tool
    calls = []
    @tool("search_github_issues")
    def fail() -> str:
        """Search issues."""
        calls.append("tool")
        raise ValueError("token=TEST_ONLY_SECRET")
    def discovery(state, **kwargs):
        calls.append("model")
        return {"discovery_status": "pending", "status": "running", "messages": [AIMessage(
            content="", tool_calls=[{"name": "search_github_issues", "args": {}, "id": "search-1"}],
        )]}
    monkeypatch.setattr(graph_module, "discovery", discovery)
    monkeypatch.setattr(graph_module, "build_github_tools", lambda *args, **kwargs: {"search_github_issues": fail})
    monkeypatch.setattr(graph_module, "build_repository_tools", lambda *args, **kwargs: {})
    monkeypatch.setattr(Graph, "draw_mermaid_png", lambda self, **kwargs: b"")
    monkeypatch.setattr(graph_module, "open", mock_open(), raising=False)
    agent = graph_module.SherpaAgent.__new__(graph_module.SherpaAgent)
    agent.settings = Settings(github_token="fake", google_api_key="fake")
    agent.github_client = object()
    agent.tools = {"discovery": ["search_github_issues"], "triage": [], "ingestion": []}
    agent.deps = SimpleNamespace(**{name: FakeLLM() for name in ["discovery", "triage", "ingestion", "advisory", "investigation"]})
    agent.create_graph()
    result = agent._graph.invoke(BugFixingState(), config={"recursion_limit": 6})
    assert calls == ["model", "tool"]
    assert result["discovery_status"] == "failed"
    assert result["workflow_error"].category == "invalid_input"
    assert "TEST_ONLY_SECRET" not in result["workflow_error"].message
