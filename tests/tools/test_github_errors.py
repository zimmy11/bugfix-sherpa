"""Safe GitHub tool error payloads, without external requests."""

import json

import pytest
from github.GithubException import GithubException

from src.tools.github import build_github_tools


class FailingClient:
    def __init__(self, status=401, data=None, headers=None):
        self.status = status
        self.data = data if data is not None else {"message": "Bad credentials token=TEST_ONLY_SECRET"}
        self.headers = headers

    def get_repo(self, name):
        raise GithubException(status=self.status, data=self.data, headers=self.headers)


def _thread_tool(client=None):
    return build_github_tools(
        client if client is not None else FailingClient(), discovery_queries=["is:issue"], limit=1,
        min_stars=0, repository_inactivity_days=365,
    )["read_issue_thread"]


def test_github_diagnostics_do_not_expose_credentials_in_tool_payload():
    results = _thread_tool().invoke({"issue_candidates": [{
        "repository_full_name": "owner/project", "issue_number": 42,
    }]})
    assert len(results) == 1
    assert results[0]["repository_full_name"] == "owner/project"
    assert results[0]["issue_number"] == 42
    assert results[0]["error"]
    assert "TEST_ONLY_SECRET" not in json.dumps(results)


@pytest.mark.parametrize("candidate", [
    {"repository_full_name": None, "issue_number": 42},
    {"repository_full_name": "invalid", "issue_number": 42},
    {"repository_full_name": "owner/project", "issue_number": 0},
])
def test_invalid_tool_input_is_rejected_before_contacting_github(candidate):
    results = _thread_tool().invoke({"issue_candidates": [candidate]})
    assert len(results) == 1
    assert results[0]["error"]
    assert "TEST_ONLY_SECRET" not in json.dumps(results)



@pytest.mark.parametrize(
    ("status", "headers", "provider_message", "retryable"),
    [
        (400, {}, "Invalid request", False),
        (401, {}, "Bad credentials", False),
        (403, {}, "Forbidden", False),
        (403, {"X-RateLimit-Remaining": "0"}, "Quota", True),
        (403, {"Retry-After": "60"}, "Quota", True),
        (403, {}, "API rate limit exceeded", True),
        (404, {}, "Not found", False),
        (422, {}, "Validation failed", False),
        (429, {}, "Too many requests", True),
        (503, {}, "Service unavailable", True),
    ],
)
def test_safe_error_preserves_http_status_and_retryability(status, headers, provider_message, retryable):
    client = FailingClient(
        status=status, data={"message": provider_message + " TEST_ONLY_SECRET"}, headers=headers,
    )
    result = _thread_tool(client).invoke({"issue_candidates": [{
        "repository_full_name": "owner/project", "issue_number": 42,
    }]})[0]
    assert result["http_status"] == status
    assert result["retryable"] is retryable
    assert result["workflow_error"]["phase"] == "triage"
    assert result["workflow_error"]["category"] == ("invalid_input" if status in {400, 422} else "operational")
    assert result["workflow_error"]["retryable"] is retryable
    assert "TEST_ONLY_SECRET" not in json.dumps(result)


def test_unstructured_provider_error_is_not_copied():
    result = _thread_tool(FailingClient(data="Unknown format TEST_ONLY_SECRET")).invoke({
        "issue_candidates": [{"repository_full_name": "owner/project", "issue_number": 42}],
    })[0]
    assert result["http_status"] == 401
    assert "TEST_ONLY_SECRET" not in json.dumps(result)



def test_github_client_does_not_add_another_retry_layer(monkeypatch):
    import src.tools.github as github_tools
    captured = {}
    def fake_client(**kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(github_tools, "Github", fake_client)
    github_tools.create_github_client("test-only-token")
    assert captured["retry"] is None
