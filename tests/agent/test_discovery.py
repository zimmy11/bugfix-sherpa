"""Offline checks for Discovery's tool result handling."""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent.nodes.discovery import discovery_node
from src.agent.schema import IssueCandidate, WorkflowError
from src.agent.state import BugFixingState
from src.utils.config import Settings


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def invoke(self, messages):
        return self.response


@pytest.fixture
def settings():
    return Settings(github_token="fake", google_api_key="fake", api_max_retries=1, github_max_results=2)


def _candidate(number, title="Bug"):
    return {
        "repository_full_name": "owner/project",
        "issue_number": number,
        "title": title,
        "url": f"https://github.com/owner/project/issues/{number}",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "repository_stats": {
            "stars": 100,
            "forks": 1,
            "open_issues": 1,
            "default_branch": "main",
            "last_pushed_at": "2026-01-01T00:00:00+00:00",
            "has_issues": True,
        },
    }


def _tool_message(status, candidates, message=""):
    return ToolMessage(
        name="search_github_issues",
        tool_call_id="search-1",
        content=json.dumps({
            "status": status,
            "message": message,
            "candidates": candidates,
        }),
    )


def _run(settings, messages, response=None):
    state = BugFixingState(messages=messages)
    llm = FakeLLM(response or AIMessage(content="done"))
    return discovery_node(state, llm, settings)


def test_tool_call_keeps_discovery_pending(settings):
    response = AIMessage(
        content="",
        tool_calls=[{"name": "search_github_issues", "args": {}, "id": "search-1"}],
    )

    update = _run(settings, [], response)

    assert update["discovery_status"] == "pending"
    assert update["messages"] == [response]
    assert "issue_candidates" not in update


@pytest.mark.parametrize("tool_status", ("completed", "partial_results"))
def test_valid_search_result_reaches_completed(settings, tool_status):
    update = _run(settings, [_tool_message(tool_status, [_candidate(42)])])

    assert update["discovery_status"] == "completed"
    assert update["status"] == "running"
    assert update["workflow_error"] is None
    assert len(update["issue_candidates"]) == 1
    assert isinstance(update["issue_candidates"][0], IssueCandidate)


def test_no_results_stops_without_candidates(settings):
    update = _run(settings, [_tool_message("no_results", [])])

    assert update["discovery_status"] == "no_results"
    assert update["status"] == "completed"
    assert update["issue_candidates"] == []


@pytest.mark.parametrize("tool_status", ("rate_limited", "partial_rate_limited"))
def test_rate_limit_is_failure_even_with_partial_candidates(settings, tool_status):
    candidates = [_candidate(42)] if tool_status == "partial_rate_limited" else []
    update = _run(settings, [_tool_message(tool_status, candidates, "Quota esaurita")])

    assert update["discovery_status"] == "failed"
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "operational"
    assert error.retryable is True
    assert error.message.strip()
    assert len(update["issue_candidates"]) == len(candidates)


def test_latest_search_result_controls_status(settings):
    messages = [
        _tool_message("completed", [_candidate(42)]),
        _tool_message("rate_limited", [], "Quota esaurita"),
    ]

    update = _run(settings, messages)

    assert update["discovery_status"] == "failed"
    assert update["status"] == "failed"
    assert update["issue_candidates"] == []


def test_candidates_are_deduplicated_and_bounded(settings):
    candidates = [_candidate(42), _candidate(42, "Older duplicate"),
                  _candidate(43), _candidate(44)]
    message = _tool_message("completed", candidates)

    first = _run(settings, [message])
    second = _run(settings, [message])

    assert [item.issue_number for item in first["issue_candidates"]] == [42, 43]
    assert first["issue_candidates"][0].title == "Bug"
    assert first["issue_candidates"] == second["issue_candidates"]


@pytest.mark.parametrize(
    "message",
    [
        ToolMessage(name="search_github_issues", tool_call_id="bad-1", content="{broken"),
        _tool_message("unknown", [_candidate(42)]),
        _tool_message("completed", [{"issue_number": 42}]),
        _tool_message("completed", []),
        _tool_message("no_results", [_candidate(42)]),
        ToolMessage(name="search_github_issues", tool_call_id="bad-2", content="[]"),
        ToolMessage(
            name="search_github_issues", tool_call_id="bad-3",
            content=json.dumps({"status": "completed"}),
        ),
    ],
)
def test_invalid_tool_result_fails_closed(settings, message):
    update = _run(settings, [message])

    assert update["discovery_status"] == "failed"
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "invariant_violation"
    assert error.retryable is False
    assert error.message.strip()
    assert update["issue_candidates"] == []


def test_llm_text_without_tool_result_is_failure(settings):
    update = _run(settings, [])

    assert update["discovery_status"] == "failed"
    assert update["status"] == "failed"
    assert update["issue_candidates"] == []



def test_missing_tool_result_produces_a_structured_invariant_error(settings):
    update = _run(settings, [])
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "invariant_violation"
    assert error.retryable is False


def test_success_clears_a_previous_workflow_error(settings):
    state = BugFixingState(
        messages=[_tool_message("completed", [_candidate(42)])],
        workflow_error=WorkflowError(
            phase="discovery", category="operational",
            message="Temporary failure", retryable=True,
        ),
    )
    update = discovery_node(state, FakeLLM(AIMessage(content="done")), settings)
    assert update["workflow_error"] is None
    restored = BugFixingState.model_validate({**state.model_dump(), **update})
    assert restored.workflow_error is None


def test_rate_limit_does_not_copy_sensitive_tool_diagnostics(settings):
    secret = "TEST_ONLY_DISCOVERY_SECRET"
    update = _run(settings, [_tool_message("rate_limited", [], f"token={secret}")])
    assert secret not in update["workflow_error"].message


def test_malformed_tool_json_does_not_leak_its_content(settings):
    secret = "TEST_ONLY_BAD_JSON_SECRET"
    message = ToolMessage(
        name="search_github_issues", tool_call_id="bad-secret",
        content=f"broken JSON token={secret}",
    )
    update = _run(settings, [message])
    assert secret not in update["workflow_error"].message


def test_llm_timeout_becomes_a_retryable_discovery_error(settings):
    class TimeoutLLM:
        def invoke(self, messages):
            raise TimeoutError("token=TEST_ONLY_TIMEOUT_SECRET")

    update = discovery_node(BugFixingState(), TimeoutLLM(), settings)
    assert update["discovery_status"] == "failed"
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "operational"
    assert error.retryable is True
    assert "TEST_ONLY_TIMEOUT_SECRET" not in error.message
