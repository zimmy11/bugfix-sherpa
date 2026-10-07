from __future__ import annotations
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Optional, Any, Literal
from src.utils.repository_safety import (
    build_public_clone_url,
    build_repository_workspace,
    resolve_inside_workspace,
    validate_repository_full_name,
)
from src.utils.config import Settings
from .schema import (
    CloneRepositoryResult,
    InspectRepositoryResult,
    IssueCandidate,
    RepositoryStats,
    InvestigationFinding,
    SherpaReport,
)
import re

class BugFixingState(BaseModel):
    '''
    State class for the bug fixing process. It holds the current state of the bug fixing process, including the current step, the identified bug location, and the suggested fix.
    '''
 # Messaggi scambiati con i modelli
    messages: Annotated[list[AnyMessage], add_messages] = Field(
        default_factory=list
    )

    # Input iniziale
    github_query: Optional[list[str]] = None
    labels: list[str] = Field(default_factory=list)
    language: Optional[str] = None
    max_results: int = Field(default=10, ge=1, le=100)

    # Issue selezionata
    issue_number: Optional[int] = None
    issue_url: Optional[str] = None
    issue_title: Optional[str] = None
    issue_body: Optional[str] = None
    issue_comments: list[str] = Field(default_factory=list)
    issue_timeline_events: list[dict[str, Any]] = Field(
        default_factory=list
    )
    issue_work_claim_signals: list[dict[str, Any]] = Field(
        default_factory=list
    )
    selected_issue: IssueCandidate | None = None

    # Risultati Discovery
    issue_candidates: list[IssueCandidate] = Field(
        default_factory=list
    )

    discovery_status: Literal["pending", "completed", "no_results", "failed"] = "pending"
    discovery_error: str | None = None

    # Repository GitHub
    repository_full_name: Optional[str] = None
    default_branch: Optional[str] = None
    repository_stats: RepositoryStats | None = None

    # Risultati Triage
    triage_status: Literal["pending", "accepted", "rejected", "failed"] = "pending"
    triage_reason: Optional[str] = None

    # Repository locale e Ingestion
    ingestion_status: Literal["pending", "cloning", "inspecting", "completed", "failed"] = "pending"
    ingestion_error: Optional[str] = None
    ingestion_attempts: int = Field(default=0, ge=0)
    ingestion_warnings: list[str] = Field(default_factory=list)
    local_repo_path: Optional[str] = None
    repository_remote_url: Optional[str] = None
    repository_revision: Optional[str] = None
    repository_tree: list[str] = Field(default_factory=list)
    repository_tree_truncated: bool = False
    truncated_files: list[str] = Field(default_factory=list)
    repository_guides: dict[str, str] = Field(default_factory=dict)
    project_manifests: dict[str, str] = Field(default_factory=dict)
    project_language: Optional[str] = None
    package_manager: Optional[str] = None
    python_version_constraint: Optional[str] = None
    test_framework: Optional[str] = None
    test_config_files: list[str] = Field(default_factory=list)

    # Inspection
    inspection_status: Literal["not_started", "completed", "partial", "failed"] = "not_started"
    repository_branch: Optional[str] = None
    inspected_repo_path: Optional[str] = None
    inspected_commit_sha: Optional[str] = None
    inspected_branch: Optional[str] = None
    inspected_sections: list[Literal["tree", "guides", "manifests", "test_config"]] = Field(default_factory=list)



    # Investigation
    files_analyzed: list[str] = Field(
        default_factory=list
    )
    search_results: list[dict[str, Any]] = Field(
        default_factory=list
    )
    relevant_symbols:list[dict[str, Any]] = Field(
        default_factory=list
    )
    investigation_findings: list[InvestigationFinding] = Field(
    default_factory=list
)
    error_signatures: list[str] = Field(
        default_factory=list
    )
    declared_reproduction_steps: list[str] = Field(
        default_factory=list
    )
    verified_reproduction_steps: list[str] = Field(
        default_factory=list
    )
    is_issue_feasible: Optional[bool] = None


    # Esecuzione dei test
    test_command: Optional[str] = None
    test_logs: Optional[str] = None
    tests_passed: Optional[bool] = None
    test_exit_code: Optional[int] = None
    test_timeout: bool = False

    # Ipotesi tecnica
    current_hypothesis: Optional[str] = None
    root_cause: Optional[str] = None
    confidence_score: Optional[float] = None

    # Advisory e report
    report: Optional[SherpaReport] = None
    final_report: Optional[str] = None

    # Human-in-the-loop
    human_feedback: Optional[str] = None
    approval_status: Optional[str] = None

    # Controllo del workflow
    current_node: Optional[str] = None
    status: str = "initialized"
    error_message: Optional[str] = None
    retry_count: int = Field(default=0, ge=0)


def _merge_by_key(
    current: list[Any], new: list[Any], key,
) -> list[Any]:
    """Keep the latest value for each key, preserving first-seen order."""
    merged: dict[Any, Any] = {}
    for item in (*current, *new):
        merged[key(item)] = item.copy() if isinstance(item, dict) else item
    return list(merged.values())


def _investigation_record_key(record: dict[str, Any], *fields: str) -> tuple[Any, ...]:
    try:
        return tuple(record[field] for field in fields)
    except KeyError as exc:
        raise ValueError(f"Risultato Investigation senza campo chiave: {exc.args[0]}") from exc


def investigation_collection_update(
    state: BugFixingState,
    *,
    files_analyzed: list[str] | None = None,
    search_results: list[dict[str, Any]] | None = None,
    relevant_symbols: list[dict[str, Any]] | None = None,
    error_signatures: list[str] | None = None,
) -> dict[str, list[Any]]:
    """Return full replacement collections after merging new investigation data."""
    return {
        "files_analyzed": _merge_by_key(
            state.files_analyzed, files_analyzed or [], lambda path: path
        ),
        "search_results": _merge_by_key(
            state.search_results, search_results or [],
            lambda item: _investigation_record_key(item, "file", "line", "query"),
        ),
        "relevant_symbols": _merge_by_key(
            state.relevant_symbols, relevant_symbols or [],
            lambda item: _investigation_record_key(item, "file", "symbol", "line"),
        ),
        "error_signatures": _merge_by_key(
            state.error_signatures, error_signatures or [],
            lambda signature: " ".join(signature.split()).casefold(),
        ),
    }


def validate_selected_issue(state: BugFixingState) -> IssueCandidate:
    if state.triage_status != "accepted" or state.selected_issue is None:
        raise ValueError("Triage non ha selezionato una issue")

    selected = state.selected_issue
    candidate = next(
        (
            item for item in state.issue_candidates
            if item.repository_full_name == selected.repository_full_name
            and item.issue_number == selected.issue_number
        ),
        None,
    )
    if candidate is None:
        raise ValueError("Issue selezionata non presente nelle candidate")

    if selected.url != candidate.url or state.issue_url != candidate.url:
        raise ValueError("URL della issue incoerente")

    if state.repository_full_name != candidate.repository_full_name:
        raise ValueError("Repository selezionata incoerente")

    if state.issue_number != candidate.issue_number:
        raise ValueError("Numero issue incoerente")

    expected_branch = candidate.repository_stats.default_branch
    if state.default_branch != expected_branch:
        raise ValueError("Default branch incoerente")

    return candidate



_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def clone_checkout_update(result: CloneRepositoryResult) -> dict[str, Any]:
    """Map verified clone metadata to the corresponding State fields."""
    if result.status == "failed":
        raise ValueError("Un clone fallito non fornisce uno snapshot del checkout")
    if not all((result.local_repo_path, result.remote_url, result.default_branch)):
        raise ValueError("Metadati del clone incompleti")
    if not result.commit_sha or not _SHA_PATTERN.fullmatch(result.commit_sha):
        raise ValueError("SHA del clone mancante o non valido")
    return {
        "local_repo_path": result.local_repo_path,
        "repository_remote_url": result.remote_url,
        "repository_branch": result.default_branch,
        "repository_revision": result.commit_sha,
    }


def inspection_checkout_update(result: InspectRepositoryResult) -> dict[str, Any]:
    """Map an inspection result without discarding clone metadata on failure."""
    update: dict[str, Any] = {
        "inspection_status": result.status,
        "ingestion_warnings": list(result.warnings),
        "inspected_sections": list(result.inspected_sections),
    }
    if result.status == "failed":
        return update
    update.update({
        "inspected_repo_path": result.repository_path,
        "inspected_commit_sha": result.inspected_commit_sha,
        "inspected_branch": result.inspected_branch,
        "repository_tree": list(result.repository_tree),
        "repository_tree_truncated": result.repository_tree_truncated,
        "truncated_files": list(result.truncated_files),
        "repository_guides": dict(result.repository_guides),
        "project_manifests": dict(result.project_manifests),
        "project_language": result.project_language,
        "package_manager": result.package_manager,
        "python_version_constraint": result.python_version_constraint,
        "test_framework": result.test_framework,
        "test_config_files": list(result.test_config_files),
    })
    return update


def validate_ingestion_snapshot(state: BugFixingState, settings: Settings) -> None:
    if state.ingestion_status != "completed":
        raise ValueError("Ingestion non completata")
    if state.inspection_status not in {"completed", "partial"}:
        raise ValueError("Ispezione del checkout non completata")
    validate_selected_issue(state)

    if not state.local_repo_path:
        raise ValueError("Path del checkout mancante")
    if not state.repository_remote_url:
        raise ValueError("Remote del checkout mancante")
    if not state.repository_revision or not _SHA_PATTERN.fullmatch(
        state.repository_revision
    ):
        raise ValueError("SHA del checkout mancante o non valido")
    if not state.inspected_commit_sha or not _SHA_PATTERN.fullmatch(
        state.inspected_commit_sha
    ):
        raise ValueError("SHA ispezionato mancante o non valido")
    if state.repository_revision.lower() != state.inspected_commit_sha.lower():
        raise ValueError("SHA ispezionato diverso da quello del clone")
    owner, repo = validate_repository_full_name(state.repository_full_name)
    expected_remote = build_public_clone_url(owner, repo)

    if state.repository_remote_url != expected_remote:
        raise ValueError("Remote Repository non coerente con l'URL pubblico atteso")

    if not state.inspected_repo_path or not state.inspected_repo_path.strip():
        raise ValueError("Path del checkout ispezionato mancante")
    if not state.repository_branch or not state.repository_branch.strip():
        raise ValueError("Branch del clone mancante")
    if not state.inspected_branch or not state.inspected_branch.strip():
        raise ValueError("Branch ispezionato mancante")

    clone_path = resolve_inside_workspace(settings.workspace_root, state.local_repo_path)
    inspected_path = resolve_inside_workspace(
        settings.workspace_root, state.inspected_repo_path
    )
    if clone_path != inspected_path:
        raise ValueError("Path locale non coerente con quello ispezionato")
    expected_path = resolve_inside_workspace(
        settings.workspace_root,
        build_repository_workspace(settings.workspace_root, owner, repo, state.issue_number),
    )
    if clone_path != expected_path:
        raise ValueError("Path del checkout diverso da quello della issue selezionata")
    if state.default_branch != state.repository_branch or (state.repository_branch != state.inspected_branch):
        raise ValueError("Branch Ispezionato diverso dal Branch di default")

    # Revalidate the assembled State: nodes may update fields separately from
    # the original InspectRepositoryResult.
    InspectRepositoryResult(
        status=state.inspection_status,
        repository_path=state.inspected_repo_path,
        inspected_commit_sha=state.inspected_commit_sha,
        inspected_branch=state.inspected_branch,
        inspected_sections=state.inspected_sections,
        repository_tree=state.repository_tree,
        repository_tree_truncated=state.repository_tree_truncated,
        truncated_files=state.truncated_files,
        repository_guides=state.repository_guides,
        project_manifests=state.project_manifests,
        project_language=state.project_language,
        package_manager=state.package_manager,
        python_version_constraint=state.python_version_constraint,
        test_framework=state.test_framework,
        test_config_files=state.test_config_files,
        warnings=state.ingestion_warnings,
    )

    exposed_paths = (
        set(state.repository_tree)
        | state.repository_guides.keys()
        | state.project_manifests.keys()
        | set(state.test_config_files)
    )
    if len(exposed_paths) > settings.max_repository_tree_entries:
        raise ValueError("Troppi path esposti nello snapshot")
    if any(
        len(path.split("/")) > settings.max_repository_tree_depth
        for path in exposed_paths
    ):
        raise ValueError("Profondità dei path oltre il limite")
    if any(
        len(content) > settings.max_guide_chars
        for content in state.repository_guides.values()
    ):
        raise ValueError("Guida oltre il limite di caratteri")
    contents = (*state.repository_guides.values(), *state.project_manifests.values())
    if any(
        len(content.encode("utf-8")) > settings.max_repository_file_bytes
        for content in contents
    ):
        raise ValueError("Contenuto oltre il limite di byte per file")
    if sum(len(content) for content in contents) > settings.max_ingestion_total_chars:
        raise ValueError("Snapshot oltre il budget totale di caratteri")
