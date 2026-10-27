"""Bounded, offline Triage thread reads."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.tools.github import build_github_tools


NOW = datetime.now(UTC)


class CountingPage:
    def __init__(self, items):
        self.items = items
        self.requested = []

    def __getitem__(self, index):
        self.requested.append(index)
        return self.items[index]


class Client:
    def __init__(self, comments, events):
        self.comments = CountingPage(comments)
        self.events = events
        self.issues_read = []

    def get_repo(self, name):
        client = self

        class Repo:
            full_name = name

            def get_issue(self, number):
                client.issues_read.append(number)
                return SimpleNamespace(
                    number=number, title="Bug", body="B" * 1000, state="open",
                    html_url=f"https://github.com/{name}/issues/{number}",
                    labels=[], assignees=[], created_at=NOW, updated_at=NOW,
                    get_comments=lambda: client.comments,
                    get_timeline=lambda: iter(client.events),
                )

        return Repo()


def _tool(client, *, limit=2, comments=2, events=2, chars=1500):
    return build_github_tools(
        client, discovery_queries=["is:issue"], limit=limit, min_stars=0,
        repository_inactivity_days=7,
        triage_max_comments_per_issue=comments,
        triage_max_timeline_events_per_issue=events,
        triage_max_total_chars=chars,
    )["read_issue_thread"]


def _candidate(number):
    return {"repository_full_name": "owner/project", "issue_number": number}


def _comment(number):
    return SimpleNamespace(id=number, user=None, body="C" * 1000,
                           created_at=NOW, updated_at=NOW)


def _event(number):
    return SimpleNamespace(raw_data={"event": "labeled", "created_at": NOW.isoformat(),
                                     "label": {"name": f"label-{number}"}})


def test_thread_read_is_bounded_and_describes_omissions():
    client = Client([_comment(n) for n in range(10)], [_event(n) for n in range(10)])
    results = _tool(client, limit=1, comments=2, events=2, chars=1500).invoke({
        "issue_candidates": [_candidate(42), _candidate(43)],
    })

    assert client.issues_read == [42]
    assert len(results) == 1
    assert len(json.dumps(results, ensure_ascii=False, separators=(",", ":"))) <= 1500
    thread = results[0]
    assert len(thread["comments"]) <= 2
    assert len(thread["timeline_events"]) <= 2
    assert thread["truncation"]["candidates_omitted"] == 1
    assert thread["truncation"]["body_has_more"]
    assert thread["truncation"]["comments_has_more"]
    assert thread["truncation"]["timeline_has_more"]
    assert thread["truncation"]["last_included_comment_id"] == (
        thread["comments"][-1]["id"] if thread["comments"] else None
    )
    assert client.comments.requested[0] == slice(None, 3, None)


def test_candidate_limit_counts_failed_reads_too():
    class FailingClient:
        def __init__(self):
            self.calls = 0

        def get_repo(self, name):
            from github.GithubException import GithubException
            self.calls += 1
            raise GithubException(404, {"message": "Missing"})

    client = FailingClient()
    results = _tool(client, limit=1).invoke({
        "issue_candidates": [_candidate(42), _candidate(43)],
    })
    assert client.calls == 1
    assert len(results) == 1


def test_too_small_budget_fails_explicitly():
    client = Client([], [])
    with pytest.raises(ValueError, match="Budget Triage insufficiente"):
        _tool(client, chars=50).invoke({"issue_candidates": [_candidate(42)]})
