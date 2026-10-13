"""Offline checks for deterministic Discovery search filtering."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import json

import pytest

from src.agent.schema import SearchIssuesResult
from src.tools.github import build_github_tools


def issue(number, *, state="open", body="Bug details", labels=(), assignees=()):
    return SimpleNamespace(
        number=number, state=state, body=body, title=f"Bug {number}",
        labels=[SimpleNamespace(name=name) for name in labels],
        assignees=list(assignees),
        repository_url="https://api.github.com/repos/owner/project",
        html_url=f"https://github.com/owner/project/issues/{number}",
        updated_at=datetime.now(UTC),
    )


def repository(*, archived=False, has_issues=True, pushed_at=None):
    return SimpleNamespace(
        full_name="owner/project", archived=archived, has_issues=has_issues,
        stargazers_count=100, forks_count=4, open_issues_count=7,
        default_branch="main", pushed_at=pushed_at or datetime.now(UTC),
        license=SimpleNamespace(spdx_id="MIT"),
    )


class SearchResults:
    def __init__(self, issues):
        self.issues = issues

    def get_page(self, page):
        return self.issues if page == 0 else []


class Client:
    def __init__(self, issues, repo=None):
        self.issues = issues
        self.repo = repo or repository()

    def search_issues(self, **kwargs):
        return SearchResults(self.issues)

    def get_repo(self, name):
        assert name == "owner/project"
        return self.repo


def search(issues, *, repo=None, limit=2, excluded_labels=("duplicate", "wontfix")):
    tool = build_github_tools(
        Client(issues, repo), discovery_queries=["is:issue"], limit=limit,
        min_stars=0, repository_inactivity_days=7,
        excluded_labels=excluded_labels,
    )["search_github_issues"]
    return tool.invoke({})


def test_filters_before_collecting_and_deduplicates():
    result = search([
        issue(1, state="closed"), issue(2, body=None), issue(3, body="  "),
        issue(4, labels=[" Duplicate "]), issue(5, labels=["WONTFIX"]),
        issue(6, assignees=["maintainer"]), issue(7), issue(7), issue(8),
    ])

    validated = SearchIssuesResult.model_validate(result)
    assert validated.status == "completed"
    assert [candidate.issue_number for candidate in validated.candidates] == [7, 8]
    assert set(result) == {"status", "message", "candidates"}
    serialized = json.loads(json.dumps(result))
    assert serialized["candidates"][0]["repository_stats"]["stars"] == 100


@pytest.mark.parametrize("repo", [
    repository(archived=True),
    repository(has_issues=False),
    repository(pushed_at=datetime.now(UTC) - timedelta(days=8)),
])
def test_unavailable_repository_yields_no_results(repo):
    result = search([issue(1)], repo=repo)
    assert result["status"] == "no_results"
    assert result["candidates"] == []


def test_partial_results_and_configurable_exclusions():
    result = search([issue(1, labels=["duplicate"])], excluded_labels=[])
    assert result["status"] == "partial_results"
    assert [candidate["issue_number"] for candidate in result["candidates"]] == [1]
