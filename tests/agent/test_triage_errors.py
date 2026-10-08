"""Target contract for migrating Triage failures to WorkflowError.

Checks run offline and verify structured failures rather than raw exceptions.
"""

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent.nodes.triage import triage_node
from src.agent.schema import IssueCandidate, WorkflowError
from src.agent.state import BugFixingState
from src.utils.config import Settings


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def invoke(self, messages):
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def settings():
    return Settings(github_token="fake", google_api_key="fake", api_max_retries=1)


@pytest.fixture
def candidate():
    return IssueCandidate(
        repository_full_name="owner/project", issue_number=42, title="Bug",
        url="https://github.com/owner/project/issues/42", updated_at="2026-01-01",
        repository_stats={
            "stars": 100, "forks": 1, "open_issues": 1, "default_branch": "main",
            "last_pushed_at": "2026-01-01", "has_issues": True,
        },
    )


def _decision(status="accepted", issue_key="owner/project#42"):
    payload = {"status": status, "reason": "Based on issue evidence"}
    if status == "accepted":
        payload["issue_key"] = issue_key
    return AIMessage(content=json.dumps(payload))


def _thread(content):
    return ToolMessage(name="read_issue_thread", tool_call_id="thread-1", content=content)


def _assert_failure(update, category="invariant_violation", retryable=False):
    assert update["triage_status"] == "failed"
    assert update["status"] == "failed"
    assert update.get("selected_issue") is None
    error = update["workflow_error"]
    assert isinstance(error, WorkflowError)
    assert error.phase == "triage"
    assert error.category == category
    assert error.retryable is retryable
    assert error.message.strip()
    assert "TEST_ONLY_SECRET" not in error.message
    return error


@pytest.mark.parametrize("content", ["", "{broken token=TEST_ONLY_SECRET", "{}"])
def test_invalid_model_response_becomes_a_structured_failure(settings, candidate, content):
    state = BugFixingState(issue_candidates=[candidate])
    update = triage_node(state, FakeLLM(AIMessage(content=content)), settings)
    _assert_failure(update)


def test_malformed_thread_json_becomes_a_structured_failure(settings, candidate):
    state = BugFixingState(
        issue_candidates=[candidate], messages=[_thread("{broken token=TEST_ONLY_SECRET")],
    )
    update = triage_node(state, FakeLLM(_decision()), settings)
    _assert_failure(update)


def test_unknown_selected_issue_becomes_a_structured_failure(settings, candidate):
    state = BugFixingState(issue_candidates=[candidate])
    update = triage_node(state, FakeLLM(_decision(issue_key="owner/project#99")), settings)
    _assert_failure(update)


def test_missing_selected_thread_becomes_a_structured_failure(settings, candidate):
    state = BugFixingState(issue_candidates=[candidate])
    update = triage_node(state, FakeLLM(_decision()), settings)
    _assert_failure(update)


def test_llm_timeout_becomes_a_safe_retryable_failure(settings, candidate):
    state = BugFixingState(issue_candidates=[candidate])
    update = triage_node(state, FakeLLM(error=TimeoutError("token=TEST_ONLY_SECRET")), settings)
    _assert_failure(update, category="operational", retryable=True)


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_valid_decision_clears_a_previous_error(settings, candidate, decision):
    state = BugFixingState(
        issue_candidates=[candidate],
        messages=[_thread(json.dumps([{
            "repository_full_name": "owner/project", "issue_number": 42,
            "body": "Reproduction", "comments": [],
        }]))],
        workflow_error=WorkflowError(phase="triage", category="operational", message="Old failure"),
    )
    update = triage_node(state, FakeLLM(_decision(status=decision)), settings)
    assert update["triage_status"] == decision
    assert update["status"] == ("running" if decision == "accepted" else "completed")
    restored = BugFixingState.model_validate({**state.model_dump(), **update})
    assert restored.workflow_error is None



def test_selected_thread_error_becomes_a_safe_structured_failure(settings, candidate):
    state = BugFixingState(
        issue_candidates=[candidate], messages=[_thread(json.dumps([{
            "repository_full_name": "owner/project", "issue_number": 42,
            "error": "Remote failure token=TEST_ONLY_SECRET",
        }]))],
    )
    update = triage_node(state, FakeLLM(_decision()), settings)
    # The legacy text payload does not yet identify a retryable cause.
    _assert_failure(update)



@pytest.mark.parametrize(("status", "can_retry", "category"), [
    (400, False, "invalid_input"), (422, False, "invalid_input"),
    (401, False, "operational"), (403, False, "operational"),
    (403, True, "operational"), (429, True, "operational"),
    (503, True, "operational"),
])
def test_github_thread_failure_uses_http_metadata(settings, candidate, status, can_retry, category):
    state = BugFixingState(issue_candidates=[candidate], messages=[_thread(json.dumps([{
        "repository_full_name": "owner/project", "issue_number": 42,
        "error": "Provider diagnostic token=TEST_ONLY_SECRET",
        "http_status": status, "retryable": can_retry,
    }]))])
    update = triage_node(state, FakeLLM(_decision()), settings)
    _assert_failure(update, category=category, retryable=can_retry)
    restored = BugFixingState.model_validate({**state.model_dump(), **update})
    assert restored.issue_candidates == [candidate]


@pytest.mark.parametrize(("code", "category", "can_retry"), [
    (400, "invalid_input", False), (401, "operational", False),
    (403, "operational", False), (429, "operational", True),
    (503, "operational", True),
])
@pytest.mark.parametrize("wrapped", [False, True])
def test_model_service_failure_is_classified(settings, candidate, code, category, can_retry, wrapped):
    from google.genai.errors import APIError
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

    error = APIError(code, {"error": {"message": "token=TEST_ONLY_SECRET"}})
    if wrapped:
        wrapper = ChatGoogleGenerativeAIError("token=TEST_ONLY_SECRET")
        wrapper.__cause__ = error
        error = wrapper
    state = BugFixingState(issue_candidates=[candidate])
    update = triage_node(state, FakeLLM(error=error), settings)
    _assert_failure(update, category=category, retryable=can_retry)
    assert update["messages"] == []
    assert "issue_candidates" not in update


def test_failure_clears_stale_selection_but_preserves_candidates(settings, candidate):
    state = BugFixingState(
        issue_candidates=[candidate], selected_issue=candidate, issue_number=42,
        repository_full_name="owner/project", default_branch="main",
    )
    update = triage_node(state, FakeLLM(AIMessage(content="")), settings)
    restored = BugFixingState.model_validate({**state.model_dump(), **update})
    assert restored.issue_candidates == [candidate]
    assert restored.selected_issue is None
    assert restored.issue_number is None
    assert restored.repository_full_name is None


def test_unexpected_programming_error_is_not_disguised_as_retryable(settings, candidate):
    update = triage_node(BugFixingState(issue_candidates=[candidate]), FakeLLM(error=RuntimeError("bug")), settings)
    _assert_failure(update, category="invariant_violation", retryable=False)
