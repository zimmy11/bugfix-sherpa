from __future__ import annotations
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Optional, Any, Literal
from operator import add
from .schema import IssueCandidate, RepositoryStats, InvestigationFinding, SherpaReport
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
    max_results: int = 10

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
    ingestion_attempts: int = 0
    ingestion_warnings: list[str] = Field(default_factory=list)
    local_repo_path: Optional[str] = None
    repository_remote_url: Optional[str] = None
    repository_revision: Optional[str] = None
    repository_tree: list[str] = Field(default_factory=list)
    repository_tree_truncated: bool = False
    repository_guides: dict[str, str] = Field(default_factory=dict)
    project_manifests: dict[str, str] = Field(default_factory=dict)
    project_language: Optional[str] = None
    package_manager: Optional[str] = None
    python_version_constraint: Optional[str] = None
    test_framework: Optional[str] = None
    test_config_files: Optional[list[str]] = None


    # Investigation
    files_analyzed: Annotated[list[str], add] = Field(
        default_factory=list
    )
    search_results: Annotated[list[dict[str, Any]], add] = Field(
        default_factory=list
    )
    relevant_symbols: Annotated[list[dict[str, Any]], add] = Field(
        default_factory=list
    )
    investigation_findings: list[InvestigationFinding] = Field(
    default_factory=list
)
    error_signatures: Annotated[list[str], add] = Field(
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
    retry_count: int = 0

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

def validate_ingestion_snapshot(state: BugFixingState) -> None:
    if state.ingestion_status != "completed":
        raise ValueError("Ingestion non completata")

    validate_selected_issue(state)

    if not state.local_repo_path:
        raise ValueError("Path del checkout mancante")
    if not state.repository_remote_url:
        raise ValueError("Remote del checkout mancante")
    if not state.repository_revision or not _SHA_PATTERN.fullmatch(
        state.repository_revision
    ):
        raise ValueError("SHA del checkout mancante o non valido")

    if state.test_config_files is None:
        raise ValueError("Snapshot di ispezione incompleto")

