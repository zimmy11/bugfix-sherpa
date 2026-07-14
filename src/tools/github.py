"""Read-only GitHub tools used by discovery and triage nodes."""
from typing import Any
from datetime import UTC, datetime, timedelta
from langchain_core.tools import tool
from github import Auth, Github
from github.GithubException import GithubException


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
        )

def build_github_tools(
    client,
    discovery_queries,
    limit,
    min_stars,
    repository_inactivity_days,
):
 
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
            A serializable dictionary containing a status, an explanatory
            message and the candidate issue list. Candidate issues include the
            repository name, issue number, title, URL, labels, assignees, a
            shortened body and repository statistics. If no suitable issue is
            found, ``candidates`` is an empty list and ``status`` is
            ``no_results``.
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
                    if exc.status == 403:
                        rate_limited = True
                        break
                    raise

                if not issues:
                    exhausted_queries.add(query)
                    continue

                next_page[query] += 1

                for issue in issues:
                    repository_full_name = issue.repository_url.removeprefix(
                        "https://api.github.com/repos/"
                    )

                    if repository_full_name not in repository_cache:
                        try:
                            repository_cache[repository_full_name] = (
                                client.get_repo(repository_full_name)
                            )
                        except GithubException as exc:
                            if exc.status == 403:
                                rate_limited = True
                                break
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
                    if issue.assignees or not issue.title or not issue.body:
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
            return {
                "status": (
                    "partial_rate_limited"
                    if candidate_list
                    else "rate_limited"
                ),
                "message": (
                    "GitHub ha interrotto la ricerca con HTTP 403. "
                    "Sono restituiti soltanto gli eventuali risultati già "
                    "raccolti; attendere il ripristino della quota prima di "
                    "ripetere Discovery."
                ),
                "candidates": candidate_list,
            }

        if len(candidate_list) < limit:
            return {
                "status": (
                    "partial_results" if candidate_list else "no_results"
                ),
                "message": (
                    "Le query di Discovery sono state esaurite. "
                    f"Trovate {len(candidate_list)} issue candidate su "
                    f"{limit} richieste."
                ),
                "candidates": candidate_list,
            }

        return {
            "status": "completed",
            "message": (
                f"Trovate {len(candidate_list)} issue candidate."
            ),
            "candidates": candidate_list,
        }
                  

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
            successful result contains the issue title, body, state, URL, labels,
            assignees, creation and update timestamps, and the complete public comment
            thread in chronological order. It also contains ``timeline_events`` and
            normalized ``work_claim_signals`` that the triage model must use to detect
            work already started by another contributor.

            Each comment contains its author, body, creation timestamp, and update
            timestamp.

            If an issue or repository is missing, inaccessible, or invalid, the
            corresponding result contains a descriptive ``error`` field associated
            with that candidate.
        """
        all_issue_threads = []
        for issue_candidate in issue_candidates:
            repository_full_name = issue_candidate.get("repository_full_name")
            issue_number = issue_candidate.get("issue_number")

            if not isinstance(repository_full_name, str):
                all_issue_threads.append({
                    "repository_full_name": repository_full_name,
                    "issue_number": issue_number,
                    "error": "repository_full_name non valido",
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
                })
                continue

            try:
                repository = client.get_repo(repository_full_name)
                issue = repository.get_issue(number=issue_number)

                comments = []
                for comment in issue.get_comments():
                    comments.append(
                        {
                            "author": (
                                comment.user.login
                                if comment.user
                                else None
                            ),
                            "body": comment.body or "",
                            "created_at": (
                                comment.created_at.isoformat()
                                if comment.created_at
                                else None
                            ),
                            "updated_at": (
                                comment.updated_at.isoformat()
                                if comment.updated_at
                                else None
                            ),
                        }
                    )

                timeline_events = []
                work_claim_signals = []
                for event in issue.get_timeline():
                    serialized_event = _serialize_timeline_event(event)
                    if serialized_event is None:
                        continue

                    timeline_events.append(serialized_event)
                    signal = _work_claim_signal(serialized_event)
                    if signal is not None:
                        work_claim_signals.append(signal)

                all_issue_threads.append({
                    "repository_full_name": repository.full_name,
                    "issue_number": issue.number,
                    "title": issue.title,
                    "body": issue.body or "",
                    "state": issue.state,
                    "url": issue.html_url,
                    "labels": [label.name for label in issue.labels],
                    "assignees": [
                        assignee.login
                        for assignee in issue.assignees
                    ],
                    "comments": comments,
                    "timeline_events": timeline_events,
                    "work_claim_signals": work_claim_signals,
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
                })
            except GithubException as exc:
                data = exc.data if isinstance(exc.data, dict) else {}
                all_issue_threads.append({
                    "repository_full_name": repository_full_name,
                    "issue_number": issue_number,
                    "error": (
                        "Impossibile leggere l'issue: "
                        f"{data.get('message', str(exc))}"
                    )
                })
        return all_issue_threads
    
    return {
        "search_github_issues": search_github_issues,
        "read_issue_thread": read_issue_thread,
    }
