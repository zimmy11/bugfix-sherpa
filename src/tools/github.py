"""Read-only GitHub tools used by discovery and triage nodes."""

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

def build_github_tools(client, discovery_queries, limit):
 
    @tool
    def search_github_issues() -> list[dict]:
        """Find candidate GitHub issues using the configured search criteria.

        Use this tool during Discovery when candidate open-source issues must
        be found. The search language, labels, activity period and maximum
        result count are already configured by Bugfix Sherpa. Call this tool
        without arguments. Do not use it to read complete issue discussions or
        inspect repository files.

        Returns:
            Candidate issues containing repository name, issue number, title,
            URL, labels, assignees and a shortened body. Duplicate issues found
            by multiple configured searches are returned only once.
        """
        candidates: dict[tuple[str, int], dict] = {}

        for query in discovery_queries:
            results = client.search_issues(
            query=query,
            sort="updated",
            order="desc",
            )

            for index, issue in enumerate(results):
                if index >= limit:
                    break

                repository_full_name = (
                    issue.repository_url
                    .removeprefix(
                        "https://api.github.com/repos/"
                    )
                )

                key = (
                    repository_full_name,
                    issue.number,
                )

                candidates[key] = {
                    "repository_full_name": (
                        repository_full_name
                    ),
                    "issue_number": issue.number,
                    "title": issue.title,
                    "url": issue.html_url,
                    "labels": [
                        label.name
                        for label in issue.labels
                    ],
                    "assignees": [
                        assignee.login
                        for assignee in issue.assignees
                    ],
                    "body_excerpt": (
                        issue.body[:1000]
                        if issue.body
                        else None
                    ),
                    "updated_at": (
                        issue.updated_at.isoformat()
                    ),
                }

                if len(candidates) >= limit:
                    break

            if len(candidates) >= limit:
                break

        return list(candidates.values())
                  

    @tool
    def get_repository_stats(repository_full_name: str):
        """Retrieve read-only health metadata for a GitHub repository.

        Use this tool during discovery after an issue search to determine whether
        the repository is active and suitable for investigation. Do not use it to
        clone the repository, modify it or inspect its local source code.

        Args:
            repository_full_name: GitHub repository in ``owner/project`` format.

        Returns:
            Repository metadata such as full name, archived status, default
            branch, stars, forks, open issue count and last push timestamp. Returns
            an explanatory error result if the URL is invalid or the repository
            cannot be read.
        """

        repository_full_name = repository_full_name.strip()
        if repository_full_name.count("/") != 1 or any(
            not part for part in repository_full_name.split("/")
        ):
            return {"error": "repository_full_name non valido"}

        try:
            repository = client.get_repo(repository_full_name)
            return {
                "repository_full_name": repository.full_name,
                "stars": repository.stargazers_count,
                "forks": repository.forks_count,
                "open_issues": repository.open_issues_count,
                "archived": repository.archived,
                "default_branch": repository.default_branch,
                "last_pushed_at": (
                    repository.pushed_at.isoformat()
                    if repository.pushed_at
                    else None
                ),
                "has_issues": repository.has_issues,
                "license": (
                    repository.license.spdx_id
                    if repository.license
                    else None
                ),
            }
        except GithubException as exc:
            return {
                "error": f"Impossibile leggere la repository: {exc.data.get('message', str(exc))}"
            }


    @tool
    def read_issue_thread(repository_full_name: str, issue_number: int,) -> dict:
        """Read an issue description and its complete public comment thread.

        Use this tool during triage when the title and short discovery excerpt are
        insufficient to evaluate feasibility, reproduction details, maintainer
        guidance or whether somebody is already working on the issue. This tool is
        read-only and must not create comments, assignments or labels.

        Args:
            repository_full_name: GitHub repository in ``owner/project`` format.
            issue_number: Positive issue number within the repository.

        Returns:
            A serializable dictionary containing issue title, body, state, labels,
            assignees and ordered comments with author and timestamps. Returns an
            explanatory error dictionary when the repository or issue is missing
            or cannot be accessed.
        """
        repository_full_name = repository_full_name.strip()

        if repository_full_name.count("/") != 1 or any(
            not part for part in repository_full_name.split("/")
        ):
            return {
                "error": "repository_full_name non valido"
            }

        if issue_number <= 0:
            return {
                "error": "issue_number deve essere positivo"
            }

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

            return {
                "repository_full_name": repository.full_name,
                "issue_number": issue.number,
                "title": issue.title,
                "body": issue.body or "",
                "state": issue.state,
                "html_url": issue.html_url,
                "labels": [label.name for label in issue.labels],
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
            }
        except GithubException as exc:
            data = exc.data if isinstance(exc.data, dict) else {}
            return {
                "error": (
                    "Impossibile leggere l'issue: "
                    f"{data.get('message', str(exc))}"
                )
            }
    
    return {
        "search_github_issues": search_github_issues,
        "get_repository_stats": get_repository_stats,
        "read_issue_thread": read_issue_thread,
    }
