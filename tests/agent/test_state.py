"""Input and counter bounds for the workflow state."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.agent.state import (
    BugFixingState,
    build_initial_state,
    investigation_collection_update,
)
from src.agent.schema import IssueCandidate, SherpaReport, WorkflowError
from src.utils.config import Settings


@pytest.mark.parametrize("field", ("ingestion_attempts", "retry_count"))
def test_counters_start_at_zero(field):
    assert getattr(BugFixingState(), field) == 0


@pytest.mark.parametrize("field", ("ingestion_attempts", "retry_count"))
@pytest.mark.parametrize("value", (-1, -2))
def test_negative_counters_are_rejected(field, value):
    with pytest.raises(ValidationError) as exc_info:
        BugFixingState(**{field: value})

    assert any(error["loc"] == (field,) for error in exc_info.value.errors())


@pytest.mark.parametrize("value", (1, 100))
def test_max_results_accepts_boundaries(value):
    assert BugFixingState(max_results=value).max_results == value


@pytest.mark.parametrize("value", (0, -1, 101))
def test_max_results_rejects_out_of_range(value):
    with pytest.raises(ValidationError) as exc_info:
        BugFixingState(max_results=value)

    assert any(error["loc"] == ("max_results",) for error in exc_info.value.errors())


def test_initial_state_has_valid_result_limit():
    assert 1 <= BugFixingState().max_results <= 100


def test_initial_workflow_phases_use_declared_pending_values():
    state = BugFixingState()

    assert state.discovery_status == "pending"
    assert state.triage_status == "pending"
    assert state.ingestion_status == "pending"
    assert state.inspection_status == "not_started"
    assert state.approval_status == "pending"


def test_build_initial_state_starts_all_workflow_phases_cleanly():
    settings = Settings(github_token="fake", google_api_key="fake")

    state = build_initial_state(request=[], settings=settings)

    assert state.discovery_status == "pending"
    assert state.triage_status == "pending"
    assert state.ingestion_status == "pending"
    assert state.inspection_status == "not_started"
    assert state.approval_status == "pending"
    assert state.workflow_error is None
    assert state.current_node is None
    assert state.status == "initialized"


def _settings(**overrides):
    settings = Settings(github_token="fake", google_api_key="fake")
    for field, value in overrides.items():
        setattr(settings, field, value)
    return settings


def test_build_initial_state_normalizes_settings_and_request():
    settings = _settings(
        github_queries=["  is:issue   is:open  ", "language:Python"],
        github_language="  Py THon  ",
        github_labels=[" help   wanted ", "good first issue", "help wanted"],
        github_max_results=25,
    )

    state = build_initial_state(
        [HumanMessage(content="  Find   issues\nfor me  ")], settings,
    )

    assert state.github_query == ["is:issue is:open", "language:Python"]
    assert state.language == "python"
    assert state.labels == ["help wanted", "good first issue"]
    assert state.max_results == 25
    assert state.messages[0].content == "Find issues for me"


@pytest.mark.parametrize("queries", ([], ["   "]))
def test_build_initial_state_rejects_empty_queries(queries):
    settings = _settings(github_queries=queries)
    with pytest.raises(ValueError, match="quer"):
        build_initial_state([], settings)


def test_build_initial_state_rejects_non_string_query():
    settings = _settings(github_queries=["is:issue", 42])
    with pytest.raises(ValueError, match="stringa"):
        build_initial_state([], settings)


@pytest.mark.parametrize("language", ("", "   "))
def test_build_initial_state_rejects_invalid_language(language):
    settings = _settings(github_language=language)
    with pytest.raises(ValueError, match="github_language"):
        build_initial_state([], settings)


@pytest.mark.parametrize("labels", ([], [" "]))
def test_build_initial_state_rejects_empty_labels(labels):
    settings = _settings(github_labels=labels)
    with pytest.raises(ValueError, match="label"):
        build_initial_state([], settings)


def test_build_initial_state_rejects_non_string_label():
    settings = _settings(github_labels=["help wanted", 7])
    with pytest.raises(ValueError, match="stringa"):
        build_initial_state([], settings)


@pytest.mark.parametrize("limit", (0, 101, 10.5))
def test_build_initial_state_rejects_invalid_result_limit(limit):
    settings = _settings(github_max_results=limit)
    with pytest.raises(ValueError, match="github_max_results"):
        build_initial_state([], settings)


def test_build_initial_state_rejects_boolean_result_limit():
    with pytest.raises(ValidationError):
        Settings(github_token="fake", google_api_key="fake", github_max_results=True)


@pytest.mark.parametrize("limit", (1, 100))
def test_build_initial_state_accepts_result_limit_boundaries(limit):
    state = build_initial_state([], _settings(github_max_results=limit))
    assert state.max_results == limit


def test_build_initial_state_rejects_non_message_request_item():
    with pytest.raises(ValueError, match="request|contenuto"):
        build_initial_state(["not a message"], _settings())


def test_initial_state_json_does_not_contain_settings_credentials_or_workspace():
    secret = "TEST_ONLY_STATE_SECRET"
    settings = Settings(
        github_token=secret,
        google_api_key="TEST_ONLY_API_KEY",
        workspace_root="C:/private/workspace",
    )

    serialized = build_initial_state([], settings).model_dump_json()

    assert secret not in serialized
    assert "TEST_ONLY_API_KEY" not in serialized
    assert "private/workspace" not in serialized


def test_empty_state_round_trips_through_json():
    original = BugFixingState()

    restored = BugFixingState.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.messages == []
    assert restored.issue_candidates == []
    assert restored.inspection_status == "not_started"


def test_populated_state_round_trips_with_messages_and_nested_models():
    candidate = IssueCandidate(
        repository_full_name="owner/project",
        issue_number=42,
        title="Bug",
        url="https://github.com/owner/project/issues/42",
        updated_at="2026-01-01T00:00:00+00:00",
        repository_stats={
            "stars": 100, "forks": 1, "open_issues": 2,
            "default_branch": "main", "last_pushed_at": "2026-01-01T00:00:00+00:00",
            "has_issues": True,
        },
    )
    report = SherpaReport(
        repository_full_name="owner/project", issue_number=42,
        issue_url=candidate.url, branch="main", commit_sha="a" * 40,
        problem_summary="Unexpected exception", investigation_status="inconclusive",
        declared_reproduction_steps=["Submit empty input"],
        investigation_limits=["Tests not run"],
    )
    original = BugFixingState(
        messages=[HumanMessage(content="Find a bug"), AIMessage(content="Found one")],
        issue_candidates=[candidate], selected_issue=candidate,
        repository_stats=candidate.repository_stats,
        report=report,
        workflow_error=WorkflowError(
            phase="investigation", category="operational",
            message="Analysis unavailable", retryable=True,
        ),
        declared_reproduction_steps=["Submit empty input"],
        verified_reproduction_steps=[],
    )

    restored = BugFixingState.model_validate_json(original.model_dump_json())

    assert restored == original
    assert isinstance(restored.messages[0], HumanMessage)
    assert isinstance(restored.messages[1], AIMessage)
    assert isinstance(restored.issue_candidates[0], IssueCandidate)
    assert restored.report.report_id == report.report_id
    assert isinstance(restored.workflow_error, WorkflowError)


def test_partial_updates_allow_a_legitimate_intermediate_state():
    initial = BugFixingState()
    discovery_started = BugFixingState.model_validate({
        **initial.model_dump(), "status": "running", "current_node": "discovery",
    })
    clone_started = BugFixingState.model_validate({
        **discovery_started.model_dump(), "ingestion_status": "cloning",
        "ingestion_attempts": 1,
    })

    assert clone_started.ingestion_status == "cloning"
    assert clone_started.inspection_status == "not_started"
    assert clone_started.local_repo_path is None


def test_repeated_investigation_snapshot_does_not_duplicate_collections():
    snapshot = {
        "files_analyzed": ["src/parser.py"],
        "search_results": [{"file": "src/parser.py", "line": 12, "query": "parse"}],
        "relevant_symbols": [{"file": "src/parser.py", "symbol": "parse", "line": 10}],
        "error_signatures": ["ValueError: invalid input"],
    }
    graph = StateGraph(BugFixingState)
    graph.add_node("first", lambda state: snapshot)
    graph.add_node("repeated", lambda state: snapshot)
    graph.add_edge(START, "first")
    graph.add_edge("first", "repeated")
    graph.add_edge("repeated", END)

    final_state = graph.compile().invoke(BugFixingState())

    for field, expected in snapshot.items():
        assert final_state[field] == expected


def test_investigation_update_returns_complete_deduplicated_collections():
    graph = StateGraph(BugFixingState)
    graph.add_node(
        "first",
        lambda state: investigation_collection_update(
            state,
            files_analyzed=["src/parser.py"],
            search_results=[{
                "file": "src/parser.py", "line": 12, "query": "parse",
                "snippet": "old",
            }],
            relevant_symbols=[{
                "file": "src/parser.py", "symbol": "parse", "line": 10,
            }],
            error_signatures=["ValueError: invalid input"],
        ),
    )
    graph.add_node(
        "second",
        lambda state: investigation_collection_update(
            state,
            files_analyzed=["src/parser.py", "src/lexer.py"],
            search_results=[{
                "file": "src/parser.py", "line": 12, "query": "parse",
                "snippet": "new",
            }],
            relevant_symbols=[{
                "file": "src/parser.py", "symbol": "parse", "line": 10,
                "detail": "confirmed",
            }],
            error_signatures=["  valueerror:   INVALID input  ", "TypeError"],
        ),
    )
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    final_state = graph.compile().invoke(BugFixingState())

    assert final_state["files_analyzed"] == ["src/parser.py", "src/lexer.py"]
    assert final_state["search_results"] == [{
        "file": "src/parser.py", "line": 12, "query": "parse",
        "snippet": "new",
    }]
    assert final_state["relevant_symbols"] == [{
        "file": "src/parser.py", "symbol": "parse", "line": 10,
        "detail": "confirmed",
    }]
    assert final_state["error_signatures"] == [
        "  valueerror:   INVALID input  ", "TypeError"
    ]


def test_investigation_update_rejects_missing_stable_key():
    with pytest.raises(ValueError, match="campo chiave: query"):
        investigation_collection_update(
            BugFixingState(), search_results=[{"file": "src/a.py", "line": 1}]
        )
