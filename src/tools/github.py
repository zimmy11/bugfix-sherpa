"""Read-only GitHub tools used by discovery and triage nodes."""
import json
from itertools import islice
from typing import Any
from datetime import UTC, datetime, timedelta
from langchain_core.tools import tool
from github import Auth, Github
from github.GithubException import GithubException
from src.agent.errors import github_rate_limited, classify_exception
from src.agent.schema import WorkflowError, SearchIssuesResult


RELEVANT_TIMELINE_EVENTS = {
    "assigned",
    "unassigned",
    "referenced",
    "cross-referenced",
    "connected",
    "disconnected",
    "closed",
    "reopened",
    "labeled",
    "unlabeled",
}


def _serialized_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _largest_fitting_prefix(text: str, fits) -> int:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(text[:middle]):
            low = middle
        else:
            high = middle - 1
    return low


def _serialize_timeline_event(event) -> dict[str, Any] | None:
    """Return the small, serializable subset needed during triage."""
    data = event.raw_data
    if not isinstance(data, dict):
        return None

    event_type = data.get("event")
    if event_type not in RELEVANT_TIMELINE_EVENTS:
        return None

    actor = data.get("actor") or data.get("user") or {}
    assignee = data.get("assignee") or {}
    source = data.get("source") or {}
    source_issue = source.get("issue") or {}
    source_pull_request = source_issue.get("pull_request") or {}

    return {
        "event": event_type,
        "actor": actor.get("login"),
        "created_at": data.get("created_at"),
        "assignee": assignee.get("login"),
        "label": (data.get("label") or {}).get("name"),
        "commit_id": data.get("commit_id"),
        "commit_url": data.get("commit_url"),
        "source": {
            "type": source.get("type"),
            "issue_number": source_issue.get("number"),
            "title": source_issue.get("title"),
            "state": source_issue.get("state"),
            "url": source_issue.get("html_url"),
            "repository_url": source_issue.get("repository_url"),
            "is_pull_request": bool(source_pull_request),
            "pull_request_url": source_pull_request.get("html_url"),
            "merged_at": source_pull_request.get("merged_at"),
        },
    }


def _work_claim_signal(
    timeline_event: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalize timeline evidence that another contributor may own."""
    event_type = timeline_event["event"]
    source = timeline_event["source"]

    if event_type == "cross-referenced" and source["is_pull_request"]:
        return {
            "kind": "linked_pull_request",
            "actor": timeline_event["actor"],
            "created_at": timeline_event["created_at"],
            "number": source["issue_number"],
            "title": source["title"],
            "state": source["state"],
            "url": source["url"] or source["pull_request_url"],
            "merged_at": source["merged_at"],
        }

    if event_type == "referenced" and (
        timeline_event["commit_id"] or timeline_event["commit_url"]
    ):
        return {
            "kind": "referenced_commit",
            "actor": timeline_event["actor"],
            "created_at": timeline_event["created_at"],
            "commit_id": timeline_event["commit_id"],
            "url": timeline_event["commit_url"],
        }

    return None


def create_github_client(token: str) -> Github:
        """Create an authenticated, read-only GitHub API client."""
        if not token or not token.strip():
            raise ValueError("GITHUB_TOKEN mancante")

        auth = Auth.Token(token.strip())

        return Github(
            auth=auth,
            timeout=20,
            retry=None,  # Retries are bounded by the workflow per operation.
        )

def build_github_tools(
    client,
    discovery_queries,
    limit,
    min_stars,
    repository_inactivity_days,
    triage_max_comments_per_issue=20,
    triage_max_timeline_events_per_issue=30,
    triage_max_total_chars=30000,
    excluded_labels=None,

):
    excluded_label_names = {
        label.strip().casefold() for label in (excluded_labels or [])
    }
 
    @tool
    def search_github_issues() -> dict[str, Any]:
        """Find candidate GitHub issues using the configured search criteria.

        Use this tool during Discovery when candidate open-source issues must
        be found. It searches open, unassigned issues directly, then retrieves
        each candidate's repository metadata. Candidates are retained only if
        their repository has at least the configured number of stars and has
        received a push within the configured activity window. Call this tool
        without arguments. Do not use it to read complete issue discussions or
        inspect repository files.

        Returns:
            A compact, JSON-serializable record validated by ``SearchIssuesResult`` with ``status``,
            ``message`` and ``candidates``. Status is ``completed`` when the
            configured candidate limit is reached, ``partial_results`` when
            the search ends with fewer candidates, or ``no_results`` when no
            candidate qualifies. A rate limit returns ``rate_limited`` if no
            candidates were collected, or ``partial_rate_limited`` with the
            candidates collected before the limit. Each candidate contains
            only serialized issue and repository fields; no PyGithub objects
            are returned.
        """

        candidates: dict[tuple[str, int], dict] = {}
        repository_cache: dict[str, Any | None] = {}
        rate_limited = False
        pushed_after = datetime.now(UTC) - timedelta(
            days=repository_inactivity_days
        )

        # Keep one paginated result set per query so every iteration fetches a
        # new page instead of repeatedly inspecting page zero.
        searches = {
            query: client.search_issues(
                query=query,
                sort="updated",
                order="desc",
            )
            for query in discovery_queries
        }
        next_page = {query: 0 for query in discovery_queries}
        exhausted_queries: set[str] = set()

        while (
            len(candidates) < limit
            and len(exhausted_queries) < len(searches)
            and not rate_limited
        ):
            for query, search_results in searches.items():
                if query in exhausted_queries:
                    continue

                try:
                    issues = search_results.get_page(next_page[query])
                except GithubException as exc:
                    if github_rate_limited(exc):
                        rate_limited = True
                        break
                    raise

                if not issues:
                    exhausted_queries.add(query)
                    continue

                next_page[query] += 1

                for issue in issues:
                    # Discards Closed Issues
                    if issue.state != "open":
                        continue
                    repository_full_name = issue.repository_url.removeprefix(
                        "https://api.github.com/repos/"
                    )
                    issue_labels = {label.name.strip().casefold() for label in issue.labels}
                    if issue_labels & excluded_label_names:
                        continue

                    if repository_full_name not in repository_cache:
                        try:
                            repository_cache[repository_full_name] = (
                                client.get_repo(repository_full_name)
                            )
                        except GithubException as exc:
                            if github_rate_limited(exc):
                                rate_limited = True
                                break
                            if exc.status != 404:
                                raise
                            repository_cache[repository_full_name] = None

                    repository = repository_cache[repository_full_name]
                    if repository is None:
                        continue
                    if repository.archived or not repository.has_issues:
                        continue
                    if repository.stargazers_count < min_stars:
                        continue
                    if repository.pushed_at is None:
                        continue

                    repository_pushed_at = repository.pushed_at
                    if repository_pushed_at.tzinfo is None:
                        repository_pushed_at = repository_pushed_at.replace(
                            tzinfo=UTC
                        )
                    if repository_pushed_at < pushed_after:
                        continue
                    if issue.assignees or not issue.title or not (issue.body or "").strip():
                        continue

                    key = (repository.full_name, issue.number)
                    candidates[key] = {
                        "repository_full_name": repository.full_name,
                        "issue_number": issue.number,
                        "title": issue.title,
                        "url": issue.html_url,
                        "labels": [item.name for item in issue.labels],
                        "assignees": [],
                        "updated_at": issue.updated_at.isoformat(),
                        "repository_stats": {
                            "stars": repository.stargazers_count,
                            "forks": repository.forks_count,
                            "open_issues": repository.open_issues_count,
                            "default_branch": repository.default_branch,
                            "last_pushed_at": repository_pushed_at.isoformat(),
                            "has_issues": repository.has_issues,
                            "license": (
                                repository.license.spdx_id
                                if repository.license
                                else None
                            ),
                        },
                    }

                    if len(candidates) >= limit:
                        break

                if rate_limited or len(candidates) >= limit:
                    break

        candidate_list = list(candidates.values())
        if rate_limited:
            return SearchIssuesResult(status = ("partial_rate_limited" if candidate_list else "rate_limited"),
                                    message = (
                    "GitHub ha interrotto la ricerca con HTTP 403. "
                    "Sono restituiti soltanto gli eventuali risultati già "
                    "raccolti; attendere il ripristino della quota prima di "
                    "ripetere Discovery."
                ),
                candidates=candidate_list).model_dump(mode="json")

        if len(candidate_list) < limit:
            return SearchIssuesResult(status = ("partial_results" if candidate_list else "no_results"),
                                    message =  (
                    "Le query di Discovery sono state esaurite. "
                    f"Trovate {len(candidate_list)} issue candidate su "
                    f"{limit} richieste."
                ),
                candidates=candidate_list).model_dump(mode="json")

        return SearchIssuesResult(status = "completed",
                                message =  (
                f"Trovate {len(candidate_list)} issue candidate."
                                            ),
                candidates=candidate_list).model_dump(mode="json")
                  

    @tool
    def read_issue_thread(issue_candidates: list[dict[str, Any]], ) -> list[dict[str, Any]]:
        """Retrieve the details and public discussion for multiple issue candidates.

        Use this read-only tool during triage to gather the context required to compare
        candidate issues. For each candidate, the tool retrieves the issue metadata,
        description, public comments, and relevant timeline events such as referenced
        commits, assignments, and linked pull requests.

        Use the returned information to identify reproduction steps, maintainer
        guidance, proposed solutions, unresolved questions, and indications that
        someone is already working on the issue.

        This tool must not modify issues or create comments, assignments, or labels.

        Args:
            issue_candidates: A list of dictionaries identifying the GitHub issues to
                retrieve. Every dictionary in the list must contain exactly the
                information required to locate one issue:

                - ``repository_full_name``: The full GitHub repository name as a string,
                using the ``owner/repository`` format. For example,
                ``"langchain-ai/langchain"``.
                - ``issue_number``: The positive integer that identifies the issue
                inside the specified repository. For example, ``123``.

                Expected input format::

                    [
                        {
                            "repository_full_name": "owner/project",
                            "issue_number": 123
                        },
                        {
                            "repository_full_name": "another-owner/another-project",
                            "issue_number": 456
                        }
                    ]

                ``repository_full_name`` and ``issue_number`` are required in every
                dictionary. Do not pass repository URLs, issue URLs, shortened
                repository names, issue titles, or issue numbers encoded as strings.

        Returns:
            A list of serializable dictionaries, one per input candidate. Each
            successful result contains the issue title, bounded body, state, URL, labels,
            assignees, creation and update timestamps, and bounded comments in
            chronological order. ``truncation`` records omitted data. It also contains ``timeline_events`` and
            normalized ``work_claim_signals`` that the triage model must use to detect
            work already started by another contributor.

            Each comment contains its author, body, creation timestamp, and update
            timestamp.

            If an issue or repository is missing, inaccessible, or invalid, the
            corresponding result contains a descriptive ``error`` field associated
            with that candidate.
        """
        all_issue_threads = []
        # Reserve room for counters and truncation reasons updated after reading.
        content_budget = max(0, triage_max_total_chars - 128)
        for issue_candidate in issue_candidates[:limit]:
            repository_full_name = issue_candidate.get("repository_full_name")
            issue_number = issue_candidate.get("issue_number")

            if not isinstance(repository_full_name, str):
                all_issue_threads.append({
                    "repository_full_name": repository_full_name,
                    "issue_number": issue_number,
                    "error": "repository_full_name non valido",
                    "workflow_error": WorkflowError(phase="triage", category="invalid_input", message="repository_full_name non valido").model_dump(mode="json"),
                })
                continue

            repository_full_name = repository_full_name.strip()

            if repository_full_name.count("/") != 1 or any(
                not part for part in repository_full_name.split("/")
            ):
                all_issue_threads.append({
                    "repository_full_name": repository_full_name,
                    "issue_number": issue_number,
                    "error": "repository_full_name non valido",
                    "workflow_error": WorkflowError(phase="triage", category="invalid_input", message="repository_full_name non valido").model_dump(mode="json"),
                })
                continue

            if (
                not isinstance(issue_number, int)
                or isinstance(issue_number, bool)
                or issue_number <= 0
            ):
                all_issue_threads.append({
                    "repository_full_name": repository_full_name,
                    "issue_number": issue_number,
                    "error": "issue_number deve essere positivo",
                    "workflow_error": WorkflowError(phase="triage", category="invalid_input", message="issue_number deve essere positivo").model_dump(mode="json"),
                })
                continue

            try:
                repository = client.get_repo(repository_full_name)
                issue = repository.get_issue(number=issue_number)

                thread = {
                    "repository_full_name": repository.full_name,
                    "issue_number": issue.number,
                    "title": issue.title,
                    "body": "",
                    "state": issue.state,
                    "url": issue.html_url,
                    "labels": [label.name for label in issue.labels],
                    "assignees": [
                        assignee.login
                        for assignee in issue.assignees
                    ],
                    "comments": [],
                    "timeline_events": [],
                    "work_claim_signals": [],
                    "truncation": {
                        "comments_included": 0,
                        "comments_has_more": False,
                        "last_included_comment_id": None,
                        "timeline_events_included": 0,
                        "timeline_has_more": False,
                        "body_chars_included": 0,
                        "body_has_more": False,
                        "truncated_comment_id": None,
                        "reason": None,
                        "candidates_omitted": max(0, len(issue_candidates) - limit),
                    },
                    "created_at": (
                        issue.created_at.isoformat()
                        if issue.created_at
                        else None
                    ),
                    "updated_at": (
                        issue.updated_at.isoformat()
                        if issue.updated_at
                        else None
                    ),
                }
                if _serialized_chars([*all_issue_threads, thread]) > triage_max_total_chars:
                    raise ValueError("Budget Triage insufficiente per i metadati del thread")

                meta = thread["truncation"]
                body = issue.body or ""
                body_length = _largest_fitting_prefix(
                    body, lambda part: _serialized_chars([
                        *all_issue_threads, {**thread, "body": part}
                    ]) <= content_budget,
                )
                thread["body"] = body[:body_length]
                meta["body_chars_included"] = body_length
                if body_length < len(body):
                    meta["body_has_more"] = True
                    meta["reason"] = "triage_max_total_chars"

                for comment in issue.get_comments()[:triage_max_comments_per_issue + 1]:
                    if len(thread["comments"]) >= triage_max_comments_per_issue:
                        meta["comments_has_more"] = True
                        meta["reason"] = meta["reason"] or "triage_max_comments_per_issue"
                        break
                    item = {
                        "id": comment.id,
                        "author": comment.user.login if comment.user else None,
                        "body": "",
                        "created_at": comment.created_at.isoformat() if comment.created_at else None,
                        "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
                    }
                    thread["comments"].append(item)
                    if _serialized_chars([*all_issue_threads, thread]) > content_budget:
                        thread["comments"].pop()
                        meta["comments_has_more"] = True
                        meta["reason"] = "triage_max_total_chars"
                        break
                    comment_body = comment.body or ""
                    length = _largest_fitting_prefix(
                        comment_body, lambda part: _serialized_chars([
                            *all_issue_threads, thread
                        ]) + _serialized_chars(part) - _serialized_chars("") <= content_budget,
                    )
                    item["body"] = comment_body[:length]
                    meta["comments_included"] += 1
                    meta["last_included_comment_id"] = comment.id
                    if length < len(comment_body):
                        meta["truncated_comment_id"] = comment.id
                        meta["comments_has_more"] = True
                        meta["reason"] = "triage_max_total_chars"
                        break

                for event_index, event in enumerate(islice(
                    issue.get_timeline(), triage_max_timeline_events_per_issue + 1
                )):
                    if event_index >= triage_max_timeline_events_per_issue:
                        meta["timeline_has_more"] = True
                        meta["reason"] = meta["reason"] or "triage_max_timeline_events_per_issue"
                        break
                    serialized_event = _serialize_timeline_event(event)
                    if serialized_event is None:
                        continue
                    thread["timeline_events"].append(serialized_event)
                    signal = _work_claim_signal(serialized_event)
                    if signal is not None:
                        thread["work_claim_signals"].append(signal)
                    if _serialized_chars([*all_issue_threads, thread]) > content_budget:
                        thread["timeline_events"].pop()
                        if signal is not None:
                            thread["work_claim_signals"].pop()
                        meta["timeline_has_more"] = True
                        meta["reason"] = "triage_max_total_chars"
                        break
                    meta["timeline_events_included"] += 1

                all_issue_threads.append(thread)
            except GithubException as exc:
                # Never expose raw provider diagnostics: they can contain
                # credentials in formats that redaction cannot recognize.
                headers = {str(key).lower(): str(value) for key, value in (exc.headers or {}).items()}
                data = exc.data if isinstance(exc.data, dict) else {}
                provider_message = data.get("message", "")
                rate_limited = exc.status == 429 or (
                    exc.status == 403 and (
                        headers.get("x-ratelimit-remaining") == "0"
                        or "retry-after" in headers
                        or (
                            isinstance(provider_message, str)
                            and "rate limit" in provider_message.lower()
                        )
                    )
                )
                if rate_limited:
                    diagnostic = "limite di quota GitHub raggiunto"
                else:
                    diagnostic = {
                        400: "richiesta GitHub non valida",
                        401: "autenticazione GitHub non valida",
                        403: "accesso GitHub non consentito",
                        404: "issue o repository non disponibile",
                        422: "parametri GitHub non validi",
                    }.get(exc.status, "errore del servizio GitHub")
                all_issue_threads.append({
                    "repository_full_name": repository_full_name,
                    "issue_number": issue_number,
                    "error": f"Impossibile leggere l'issue: {diagnostic}",
                    "http_status": exc.status,
                    "retryable": rate_limited or 500 <= exc.status < 600,
                    "workflow_error": classify_exception(exc, "triage").model_dump(mode="json"),
                })

        if _serialized_chars(all_issue_threads) > triage_max_total_chars:
            raise ValueError("Il risultato Triage supera il budget di caratteri")
        return all_issue_threads
    
    return {
        "search_github_issues": search_github_issues,
        "read_issue_thread": read_issue_thread,
    }
