"""Selective retries and safe boundaries, including actual ToolNode execution."""
from collections import Counter

import pytest
from google.genai.errors import APIError
from github.GithubException import GithubException
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

import src.agent.errors as errors
from src.agent.schema import WorkflowError
from src.agent.state import BugFixingState
from src.utils.config import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(errors, "sleep", lambda seconds: None)
    return Settings(github_token="fake", google_api_key="fake", api_max_retries=3,
                    api_retry_min_seconds=1, api_retry_max_seconds=2)


@pytest.mark.parametrize(("exception", "category", "retryable"), [
    (TimeoutError("TEST_ONLY_SECRET"), "operational", True),
    (ValueError("TEST_ONLY_SECRET"), "invalid_input", False),
    (RuntimeError("TEST_ONLY_SECRET"), "invariant_violation", False),
    (APIError(401, {"message": "TEST_ONLY_SECRET"}), "operational", False),
    (APIError(429, {"message": "TEST_ONLY_SECRET"}), "operational", True),
    (APIError(503, {"message": "TEST_ONLY_SECRET"}), "operational", True),
    (GithubException(403, {"message": "Forbidden"}), "operational", False),
    (GithubException(403, {"message": "Rate limit exceeded"}), "operational", True),
])
def test_retry_classification_is_conservative(exception, category, retryable):
    error = errors.classify_exception(exception, "discovery")
    assert error.category == category
    assert error.retryable is retryable
    assert "TEST_ONLY_SECRET" not in error.model_dump_json()


@pytest.mark.parametrize(("exception", "expected_calls"), [
    (TimeoutError("secret"), 3), (ValueError("invalid"), 1), (RuntimeError("bug"), 1),
    (APIError(401, {}), 1), (APIError(503, {}), 3),
])
def test_retry_attempts_are_bounded(settings, exception, expected_calls):
    calls = []
    def operation():
        calls.append(1)
        raise exception
    with pytest.raises(errors.WorkflowFailure):
        errors.invoke_with_retry(operation, "discovery", settings)
    assert len(calls) == expected_calls


def test_success_after_transient_failure(settings, monkeypatch):
    delays = []
    monkeypatch.setattr(errors, "sleep", delays.append)
    calls = []
    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError()
        return "done"
    assert errors.invoke_with_retry(operation, "triage", settings) == "done"
    assert delays == [1, 2]


@pytest.mark.parametrize("phase", ["ingestion", "investigation", "advisory", "human_review"])
def test_unimplemented_phase_returns_structured_error(phase):
    update = errors.phase_boundary(lambda state: None, phase)(BugFixingState())
    assert update["status"] == "failed"
    assert update["workflow_error"].phase == phase
    assert update["workflow_error"].category == "invariant_violation"
    assert update["error_message"] == update["workflow_error"].message
    if phase == "ingestion":
        assert update["ingestion_error"] == update["workflow_error"].message


def test_invalid_state_update_is_an_invariant_error():
    update = errors.phase_boundary(lambda state: {"tests_passed": True}, "investigation")(BugFixingState())
    assert update["workflow_error"].category == "invariant_violation"
    assert update["workflow_error"].retryable is False


def test_actual_tool_batch_retries_only_the_failing_read(settings):
    calls = Counter()
    @tool("read_issue_thread")
    def first() -> str:
        """Read the first item."""
        calls["first"] += 1
        return "first result"
    @tool("search_github_issues")
    def second() -> str:
        """Read the second item."""
        calls["second"] += 1
        if calls["second"] == 1:
            raise TimeoutError("TEST_ONLY_SECRET")
        return "second result"
    tools = [errors.guarded_tool(item, "discovery", settings) for item in [first, second]]
    graph = StateGraph(BugFixingState)
    graph.add_node("tools", errors.phase_boundary(ToolNode(tools, handle_tool_errors=False).invoke,
                                                 "discovery", accepts_config=True))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    state = BugFixingState(messages=[AIMessage(content="", tool_calls=[
        {"name": item.name, "args": {}, "id": item.name} for item in tools
    ])])
    result = graph.compile().invoke(state)
    assert calls == {"first": 1, "second": 2}
    assert result.get("workflow_error") is None
    assert sum(isinstance(message, ToolMessage) for message in result["messages"]) == 2


def test_clone_operation_is_not_replayed(settings):
    calls = []
    @tool("clone_selected_repository")
    def clone() -> str:
        """Clone a checkout."""
        calls.append(1)
        raise TimeoutError("TEST_ONLY_SECRET")
    guarded = errors.guarded_tool(clone, "ingestion", settings)
    with pytest.raises(errors.WorkflowFailure) as exc:
        guarded.invoke({})
    assert len(calls) == 1
    assert exc.value.error.retryable is True


def test_structured_service_failure_reaches_state(settings):
    @tool("clone_selected_repository")
    def clone() -> dict:
        """Clone a checkout."""
        return {"status": "failed", "error": WorkflowError(
            phase="ingestion", category="invariant_violation", message="Checkout mismatch",
        ).model_dump()}
    guarded = errors.guarded_tool(clone, "ingestion", settings)
    with pytest.raises(errors.WorkflowFailure) as exc:
        guarded.invoke({})
    assert exc.value.error.category == "invariant_violation"



def test_long_retry_after_does_not_issue_an_early_retry(settings, monkeypatch):
    calls, waits = [], []
    monkeypatch.setattr(errors, "sleep", waits.append)
    def operation():
        calls.append(1)
        raise GithubException(429, {}, headers={"Retry-After": "600"})
    with pytest.raises(errors.WorkflowFailure) as exc:
        errors.invoke_with_retry(operation, "discovery", settings)
    assert exc.value.error.retryable is True
    assert calls == [1]
    assert waits == []


@pytest.mark.parametrize("update", [
    {"status": "failed"},
    {"workflow_error": {"phase": "unknown"}},
    {"workflow_error": WorkflowError(phase="triage", category="operational", message="Wrong phase")},
])
def test_inconsistent_failure_update_is_not_retryable(update):
    result = errors.phase_boundary(lambda state: update, "investigation")(BugFixingState())
    assert result["workflow_error"].phase == "investigation"
    assert result["workflow_error"].category == "invariant_violation"
    assert result["workflow_error"].retryable is False



def test_invalid_triage_selection_is_caught_before_routing():
    update = errors.phase_boundary(lambda state: {"triage_status": "accepted"}, "triage")(BugFixingState())
    assert update["triage_status"] == "failed"
    assert update["workflow_error"].category == "invariant_violation"


def test_completed_ingestion_requires_verified_snapshot(settings):
    update = errors.phase_boundary(lambda state: {"ingestion_status": "completed"}, "ingestion", settings=settings)(BugFixingState())
    assert update["ingestion_status"] == "failed"
    assert update["workflow_error"].category == "invariant_violation"



def test_models_do_not_multiply_the_workflow_retry_budget(settings, monkeypatch):
    import src.agent.deps as deps
    configurations = []
    def fake_model(**kwargs):
        configurations.append(kwargs)
        return object()
    monkeypatch.setattr(deps, "ChatGoogleGenerativeAI", fake_model)
    deps.LLMNodesConfig.from_settings(settings)
    assert len(configurations) == 5
    assert all(config["max_retries"] == 1 for config in configurations)
