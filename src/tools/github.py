"""Read-only GitHub tools used by discovery and triage nodes."""
from typing import Any
from datetime import UTC, datetime, timedelta
from langchain_core.tools import tool
from github import Auth, Github
from github.GithubException import GithubException
 
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
        repository_cache: dict[str, Any] = {}
        rate_limited = False
        pushed_after = datetime.now(UTC) - timedelta(
            days=repository_inactivity_days
        )

        for query in discovery_queries:
            try:
                issues = client.search_issues(
                    query=query,
                    sort="updated",
                    order="desc",
                ).get_page(0)
            except GithubException as exc:
                if exc.status == 403:
                    rate_limited = True
                    break
                raise

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
                    "body_excerpt": issue.body[:1000],
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

            if rate_limited:
                break

            if len(candidates) >= limit:
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

        if not candidate_list:
            return {
                "status": "no_results",
                "message": (
                    "Nessuna issue aperta, non assegnata e compatibile con "
                    "i criteri di Discovery è stata trovata."
                ),
                "candidates": [],
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
        description, and complete public comment thread.

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
            thread in chronological order.

            Each comment contains its author, body, creation timestamp, and update
            timestamp.

            If an issue or repository is missing, inaccessible, or invalid, the
            corresponding result contains a descriptive ``error`` field associated
            with that candidate.
        """
        all_issue_comments = []
        for issue_candidate in issue_candidates:
            repository_full_name = issue_candidate['repository_full_name'].strip()

            if repository_full_name.count("/") != 1 or any(
                not part for part in repository_full_name.split("/")
            ):
                return {
                    "error": "repository_full_name non valido"
                }

            if issue_candidate['issue_number'] <= 0:
                return [{
                    "error": "issue_number deve essere positivo"
                }]

            try:
                repository = client.get_repo(repository_full_name)
                issue = repository.get_issue(number=issue_candidate['issue_number'])

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

                all_issue_comments.append({
                    "repository_full_name": repository.full_name,
                    "issue_number": issue.number,
                    "title": issue.title,
                    "body": issue.body or "",
                    "state": issue.state,
                    "url": issue.url,
                    "assignees": [
                        assignee.login
                        for assignee in issue.assignees
                    ],
                    "comments": comments,
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
                return [{
                    "error": (
                        "Impossibile leggere l'issue: "
                        f"{data.get('message', str(exc))}"
                    )
                }]
        return all_issue_comments
    
    return {
        "search_github_issues": search_github_issues,
        "read_issue_thread": read_issue_thread,
    }
