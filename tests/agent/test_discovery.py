"""Offline checks for Discovery's tool result handling."""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent.nodes.discovery import discovery_node
from src.agent.schema import DiscoveryOutcome, IssueCandidate, WorkflowError
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


def _tool_message(status, candidates, message="", call_id="search-1"):
    return ToolMessage(
        name="search_github_issues",
        tool_call_id=call_id,
        content=json.dumps({
            "status": status,
            "message": message,
            "candidates": candidates,
        }),
    )


def _selection(*numbers):
    return AIMessage(content=json.dumps({
        "message": "Selezione conclusa",
        "candidates": [
            {"repository_full_name": "owner/project", "issue_number": number}
            for number in numbers
        ],
    }))


def _run(settings, messages, response=None):
    history = []
    for message in messages:
        if isinstance(message, ToolMessage):
            history.append(AIMessage(content="", tool_calls=[{
                "name": "search_github_issues", "args": {}, "id": message.tool_call_id,
            }]))
        history.append(message)
    if response is None:
        numbers = []
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                try:
                    numbers = [item["issue_number"] for item in json.loads(message.content)["candidates"]
                               if isinstance(item.get("issue_number"), int)]
                except (TypeError, ValueError, KeyError):
                    pass
                break
        response = _selection(*numbers)
    state = BugFixingState(messages=history)
    llm = FakeLLM(response)
    return discovery_node(state, llm, settings)


def test_tool_call_keeps_discovery_pending(settings):
    response = AIMessage(
        content="",
        tool_calls=[{"name": "search_github_issues", "args": {}, "id": "search-1"}],
    )

    previous = DiscoveryOutcome(status="no_results")
    state = BugFixingState(
        discovery_outcome=previous,
        workflow_error=WorkflowError(
            phase="discovery", category="operational",
            message="Previous rate limit", retryable=True,
        ),
    )
    update = discovery_node(state, FakeLLM(response), settings)

    assert update["discovery_outcome"] == DiscoveryOutcome(status="pending")
    assert update["workflow_error"] is None
    restored = BugFixingState.model_validate({**state.model_dump(), **update})
    assert restored.discovery_outcome.status == "pending"
    assert restored.workflow_error is None
    assert update["messages"] == [response]


def test_last_allowed_discovery_visit_invokes_llm_and_counts_it(settings):
    settings.discovery_max_steps = 2
    call = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "search-1",
    }])
    state = BugFixingState(
        discovery_attempt=1,
        messages=[call, _tool_message("completed", [_candidate(42)])],
    )

    update = discovery_node(state, FakeLLM(_selection(42)), settings)

    assert update["discovery_attempt"] == 2
    assert update["discovery_outcome"].status == "completed"
    assert [item.issue_number for item in update["discovery_outcome"].candidates] == [42]


def test_discovery_limit_stops_before_llm_with_structured_error(settings):
    settings.discovery_max_steps = 2

    class UnexpectedLLM:
        def invoke(self, messages):
            pytest.fail("L'LLM non deve essere invocato dopo il limite")

    update = discovery_node(
        BugFixingState(discovery_attempt=2), UnexpectedLLM(), settings,
    )

    assert update["discovery_attempt"] == 2
    assert update["messages"] == []
    assert update["discovery_outcome"].status == "failed"
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "operational"
    assert error.retryable is False
    assert "passaggi" in error.message


def test_tool_call_counts_as_discovery_visit(settings):
    settings.discovery_max_steps = 2
    response = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "search-1",
    }])
    first = discovery_node(BugFixingState(), FakeLLM(response), settings)

    assert first["discovery_outcome"].status == "pending"
    assert first["discovery_attempt"] == 1

    state_after_tool = BugFixingState(
        discovery_attempt=first["discovery_attempt"],
        messages=[response, _tool_message("completed", [_candidate(42)])],
    )
    second = discovery_node(state_after_tool, FakeLLM(_selection(42)), settings)
    assert second["discovery_attempt"] == 2
    assert second["discovery_outcome"].status == "completed"


@pytest.mark.parametrize("tool_status", ("completed", "partial_results"))
def test_valid_search_result_reaches_completed(settings, tool_status):
    update = _run(settings, [_tool_message(tool_status, [_candidate(42)])])

    assert update["discovery_outcome"].status == "completed"
    assert update["status"] == "running"
    assert update["workflow_error"] is None
    assert update["discovery_outcome"].message == "Selezione conclusa"
    assert len(update["discovery_outcome"].candidates) == 1
    assert isinstance(update["discovery_outcome"].candidates[0], IssueCandidate)


def test_no_results_stops_without_candidates(settings):
    update = _run(settings, [_tool_message("no_results", [])])

    assert update["discovery_outcome"].status == "no_results"
    assert update["status"] == "completed"
    assert update["discovery_outcome"].candidates == []


@pytest.mark.parametrize("tool_status", ("rate_limited", "partial_rate_limited"))
def test_rate_limit_is_failure_even_with_partial_candidates(settings, tool_status):
    candidates = [_candidate(42)] if tool_status == "partial_rate_limited" else []
    update = _run(settings, [_tool_message(tool_status, candidates, "Quota esaurita")])

    assert update["discovery_outcome"].status == tool_status
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "operational"
    assert error.retryable is True
    assert error.message.strip()
    assert len(update["discovery_outcome"].candidates) == len(candidates)


@pytest.mark.parametrize("tool_status", ("rate_limited", "partial_rate_limited"))
def test_quota_result_stops_before_llm_and_keeps_explicit_status(settings, tool_status):
    from src.agent.errors import phase_boundary

    class UnexpectedLLM:
        def invoke(self, messages):
            pytest.fail("La quota esaurita deve fermare Discovery prima dell'LLM")

    candidates = [_candidate(42)] if tool_status == "partial_rate_limited" else []
    call = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "search-1",
    }])
    state = BugFixingState(
        discovery_attempt=1,
        messages=[call, _tool_message(tool_status, candidates, "Quota esaurita")],
    )
    update = phase_boundary(
        lambda current: discovery_node(current, UnexpectedLLM(), settings), "discovery",
    )(state)

    assert update["discovery_outcome"].status == tool_status
    assert update["status"] == "failed"
    assert update["discovery_attempt"] == 1
    assert len(update["discovery_outcome"].candidates) == len(candidates)
    assert update["workflow_error"].retryable is True


def test_repeated_search_request_fails_without_new_tool_execution(settings):
    old_call = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "old-search",
    }])
    state = BugFixingState(
        discovery_attempt=1,
        messages=[old_call, _tool_message("completed", [_candidate(42)], call_id="old-search")],
    )
    new_call = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "second-search",
    }])

    update = discovery_node(state, FakeLLM(new_call), settings)

    assert update["discovery_outcome"].status == "failed"
    assert update["status"] == "failed"
    assert update["workflow_error"].category == "invariant_violation"
    assert update["discovery_attempt"] == 2


def test_latest_search_result_controls_status(settings):
    messages = [
        _tool_message("completed", [_candidate(42)]),
        _tool_message("rate_limited", [], "Quota esaurita", call_id="search-2"),
    ]

    update = _run(settings, messages)

    assert update["discovery_outcome"].status == "rate_limited"
    assert update["status"] == "failed"
    assert update["discovery_outcome"].candidates == []


def test_candidates_are_deduplicated_and_bounded(settings):
    candidates = [_candidate(42), _candidate(42, "Older duplicate"),
                  _candidate(43), _candidate(44)]
    message = _tool_message("completed", candidates)

    first = _run(settings, [message])
    second = _run(settings, [message])

    assert [item.issue_number for item in first["discovery_outcome"].candidates] == [42, 43]
    assert first["discovery_outcome"].candidates[0].title == "Bug"
    assert first["discovery_outcome"].candidates == second["discovery_outcome"].candidates


def test_final_selection_filters_and_orders_current_tool_candidates(settings):
    update = _run(settings, [_tool_message(
        "completed", [_candidate(42), _candidate(43), _candidate(44)],
    )], response=_selection(44, 42))

    assert [item.issue_number for item in update["discovery_outcome"].candidates] == [44, 42]
    assert update["discovery_outcome"].message == "Selezione conclusa"


def test_invented_issue_is_discarded_and_original_tool_data_is_kept(settings):
    update = _run(
        settings,
        [_tool_message("completed", [_candidate(42, title="Original title")])],
        response=_selection(99, 42),
    )

    assert update["discovery_outcome"].status == "completed"
    assert [(item.repository_full_name, item.issue_number, item.title)
            for item in update["discovery_outcome"].candidates] == [
                ("owner/project", 42, "Original title"),
            ]


def test_duplicate_selected_keys_appear_only_once(settings):
    update = _run(
        settings,
        [_tool_message("completed", [_candidate(42), _candidate(43)])],
        response=_selection(43, 43, 42, 42),
    )

    assert [item.issue_number for item in update["discovery_outcome"].candidates] == [43, 42]


def test_same_issue_number_in_different_repositories_has_distinct_keys(settings):
    second = _candidate(42, title="Other repository issue")
    second["repository_full_name"] = "other/project"
    second["url"] = "https://github.com/other/project/issues/42"
    response = AIMessage(content=json.dumps({
        "message": "Entrambe pertinenti",
        "candidates": [
            {"repository_full_name": "other/project", "issue_number": 42},
            {"repository_full_name": "owner/project", "issue_number": 42},
        ],
    }))

    update = _run(settings, [_tool_message("completed", [_candidate(42), second])],
                  response=response)

    assert [(item.repository_full_name, item.issue_number)
            for item in update["discovery_outcome"].candidates] == [
                ("other/project", 42), ("owner/project", 42),
            ]


def test_old_search_results_cannot_supply_current_selection(settings):
    messages = [
        _tool_message("completed", [_candidate(42)], call_id="old"),
        _tool_message("completed", [_candidate(43)], call_id="new"),
    ]
    update = _run(settings, messages, response=_selection(42, 43))

    assert [item.issue_number for item in update["discovery_outcome"].candidates] == [43]


def test_repeated_discovery_pass_uses_only_new_search_result(settings):
    first_call = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "first",
    }])
    first_result = _tool_message("completed", [_candidate(42)], call_id="first")
    second_call = AIMessage(content="", tool_calls=[{
        "name": "search_github_issues", "args": {}, "id": "second",
    }])
    second_result = _tool_message("completed", [_candidate(42), _candidate(43)], call_id="second")

    first = discovery_node(
        BugFixingState(messages=[first_call, first_result]), FakeLLM(_selection(42)), settings,
    )
    second = discovery_node(
        BugFixingState(messages=[first_call, first_result, first["messages"][0],
                                  second_call, second_result]),
        FakeLLM(_selection(43, 42, 43)), settings,
    )

    assert [item.issue_number for item in first["discovery_outcome"].candidates] == [42]
    assert [item.issue_number for item in second["discovery_outcome"].candidates] == [43, 42]


def test_empty_final_selection_produces_no_results(settings):
    update = _run(settings, [_tool_message("completed", [_candidate(42)])],
                  response=_selection())
    assert update["discovery_outcome"].status == "no_results"
    assert update["discovery_outcome"].candidates == []


def test_invalid_final_selection_fails_closed(settings):
    update = _run(settings, [_tool_message("completed", [_candidate(42)])],
                  response=AIMessage(content="done"))
    assert update["discovery_outcome"].status == "failed"
    assert update["workflow_error"].category == "invariant_violation"


def test_state_result_limit_cannot_raise_trusted_discovery_limit(settings):
    state = BugFixingState(
        max_results=99,
        messages=[AIMessage(content="", tool_calls=[{
            "name": "search_github_issues", "args": {}, "id": "search-1",
        }]), _tool_message(
            "completed", [_candidate(42), _candidate(43), _candidate(44)],
        )],
    )

    update = discovery_node(state, FakeLLM(_selection(42, 43, 44)), settings)

    assert settings.github_max_results == 2
    assert [candidate.issue_number for candidate in update["discovery_outcome"].candidates] == [42, 43]


def test_selection_order_is_bounded_by_configured_limit(settings):
    update = _run(
        settings,
        [_tool_message("completed", [_candidate(42), _candidate(43), _candidate(44)])],
        response=_selection(44, 43, 42),
    )

    assert settings.github_max_results == 2
    assert [item.issue_number for item in update["discovery_outcome"].candidates] == [44, 43]


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

    assert update["discovery_outcome"].status == "failed"
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "invariant_violation"
    assert error.retryable is False
    assert error.message.strip()
    assert update["discovery_outcome"].candidates == []


def test_llm_text_without_tool_result_is_failure(settings):
    update = _run(settings, [], response=_selection())

    assert update["discovery_outcome"].status == "failed"
    assert update["status"] == "failed"
    assert update["discovery_outcome"].candidates == []



def test_missing_tool_result_produces_a_structured_invariant_error(settings):
    update = _run(settings, [], response=_selection())
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "invariant_violation"
    assert error.retryable is False


def test_success_clears_a_previous_workflow_error(settings):
    state = BugFixingState(
        messages=[AIMessage(content="", tool_calls=[{
            "name": "search_github_issues", "args": {}, "id": "search-1",
        }]), _tool_message("completed", [_candidate(42)])],
        workflow_error=WorkflowError(
            phase="discovery", category="operational",
            message="Temporary failure", retryable=True,
        ),
    )
    update = discovery_node(state, FakeLLM(_selection(42)), settings)
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
    assert update["discovery_outcome"].status == "failed"
    assert update["status"] == "failed"
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "discovery"
    assert error.category == "operational"
    assert error.retryable is True
    assert "TEST_ONLY_TIMEOUT_SECRET" not in error.message


def test_real_search_tool_message_is_accepted_by_discovery(settings):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from langgraph.prebuilt import ToolNode
    from langgraph.graph import StateGraph, START
    from src.tools.github import build_github_tools

    issue = SimpleNamespace(
        state="open", number=42, title="Bug", body="Steps to reproduce",
        labels=[], assignees=[], updated_at=datetime.now(UTC),
        repository_url="https://api.github.com/repos/owner/project",
        html_url="https://github.com/owner/project/issues/42",
    )
    repo = SimpleNamespace(
        full_name="owner/project", archived=False, has_issues=True,
        stargazers_count=100, forks_count=1, open_issues_count=1,
        default_branch="main", pushed_at=datetime.now(UTC), license=None,
    )

    class Client:
        def search_issues(self, **kwargs):
            return SimpleNamespace(get_page=lambda page: [issue] if page == 0 else [])

        def get_repo(self, name):
            assert name == "owner/project"
            return repo

    tool = build_github_tools(
        Client(), discovery_queries=["is:issue"], limit=2,
        min_stars=0, repository_inactivity_days=7,
        excluded_labels=["duplicate"],
    )["search_github_issues"]
    call = AIMessage(content="", tool_calls=[
        {"name": "search_github_issues", "args": {}, "id": "search-real"},
    ])
    graph = StateGraph(BugFixingState)
    graph.add_node("search", ToolNode([tool]))
    graph.add_edge(START, "search")
    tool_message = graph.compile().invoke(BugFixingState(messages=[call]))["messages"][-1]
    update = _run(settings, [call, tool_message], response=_selection(42))

    assert update["discovery_outcome"].status == "completed", tool_message.content
    assert [item.issue_number for item in update["discovery_outcome"].candidates] == [42]
