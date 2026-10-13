"""Offline integration checks of the actual graph's failure branches."""

from types import SimpleNamespace
from unittest.mock import mock_open

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables.graph import Graph

import src.agent.graph as graph_module
from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError
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
        return {"messages": [AIMessage(content="Search finished")], "discovery_outcome": DiscoveryOutcome(status="completed", candidates=[candidate])}

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
        BugFixingState(discovery_outcome=DiscoveryOutcome(status="completed", candidates=[candidate])), config={"recursion_limit": 5},
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
        return {"discovery_outcome": DiscoveryOutcome(status="pending"), "status": "running", "messages": [AIMessage(
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
    assert result["status"] == "failed"
    assert result["workflow_error"].category == "invalid_input"
    assert "TEST_ONLY_SECRET" not in result["workflow_error"].message


def test_untrusted_state_updates_cannot_change_trusted_tool_configuration(monkeypatch):
    configured = {}

    def capture_github(client, **kwargs):
        configured["github"] = kwargs
        return {}

    def capture_repository(settings):
        configured["repository_settings"] = settings
        return {}

    def discovery(state, **kwargs):
        return {
            "discovery_outcome": DiscoveryOutcome(status="no_results"),
            "max_results": 100,
            "github_token": "ATTACKER_TOKEN",
            "google_api_key": "ATTACKER_API_KEY",
            "workspace_root": "C:/attacker/workspace",
            "max_repository_file_bytes": 999999999,
        }

    settings = Settings(
        github_token="TRUSTED_GITHUB_TOKEN",
        google_api_key="TRUSTED_GOOGLE_API_KEY",
        github_max_results=2,
        max_repository_file_bytes=1234,
        workspace_root="C:/trusted/workspace",
    )
    monkeypatch.setattr(graph_module, "discovery", discovery)
    monkeypatch.setattr(graph_module, "build_github_tools", capture_github)
    monkeypatch.setattr(graph_module, "build_repository_tools", capture_repository)
    monkeypatch.setattr(Graph, "draw_mermaid_png", lambda self, **kwargs: b"")
    monkeypatch.setattr(graph_module, "open", mock_open(), raising=False)

    agent = graph_module.SherpaAgent.__new__(graph_module.SherpaAgent)
    agent.settings = settings
    agent.github_client = object()
    agent.tools = {name: [] for name in ("discovery", "triage", "ingestion")}
    agent.deps = SimpleNamespace(**{
        name: FakeLLM()
        for name in ("discovery", "triage", "ingestion", "advisory", "investigation")
    })
    agent.create_graph()
    result = agent._graph.invoke(
        BugFixingState(max_results=99), config={"recursion_limit": 4},
    )

    assert configured["github"]["limit"] == 2
    assert configured["github"]["discovery_queries"] == settings.github_queries
    assert configured["github"]["min_stars"] == settings.github_min_stars
    assert configured["github"]["excluded_labels"] == settings.github_excluded_labels
    assert configured["repository_settings"] is settings
    assert result["max_results"] == 100
    assert "github_token" not in result
    assert "google_api_key" not in result
    assert "workspace_root" not in result
    assert "max_repository_file_bytes" not in result
