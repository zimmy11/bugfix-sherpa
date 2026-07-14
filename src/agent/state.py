from __future__ import annotations
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Optional, Any, Literal
from operator import add

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
    selected_issue: Optional[dict[str, Any]] = None

    # Risultati Discovery
    issue_candidates: list[dict[str, Any]] = Field(
        default_factory=list
    )


    # Repository GitHub
    repository_full_name: Optional[str] = None
    default_branch: Optional[str] = None
    repository_stats: Optional[dict[str, Any]] = None

    # Risultati Triage
    triage_status: Optional[str] = None
    triage_reason: Optional[str] = None

    # Repository locale e Ingestion
    ingestion_status: Optional[Literal["pending", "cloning", "inspecting", "completed", "failed"]]
    ingestion_error: Optional[str] = None
    ingestion_attempts: Optional[int] = None
    ingestion_warnings: Optional[str] = None
    local_repo_path: Optional[str] = None
    repository_remote_url: Optional[str] = None
    repository_revision: Optional[str] = None
    repository_tree: list[str] = Field(default_factory=list)
    repository_tree_truncated: bool = False
    repository_guides: dict[str, str] = Field(default_factory=dict)
    project_manifests: Optional[dict[str, str]] = None
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
    investigation_findings: Annotated[list[dict[str, Any]], add] = Field(
        default_factory=list
    )
    error_signatures: Annotated[list[str], add] = Field(
        default_factory=list
    )
    reproduction_steps: Annotated[list[str], add] = Field(
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
    suggested_strategy: Optional[str] = None
    proposed_changes: list[str] = Field(default_factory=list)
    tests_to_add: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    final_report: Optional[str] = None

    # Human-in-the-loop
    human_feedback: Optional[str] = None
    approval_status: Optional[str] = None

    # Controllo del workflow
    current_node: Optional[str] = None
    status: str = "initialized"
    error_message: Optional[str] = None
    retry_count: int = 0
