from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal
from dataclasses import dataclass


class CloneRepositoryResult(BaseModel):
    status: Literal["cloned", "reused", "failed"]
    repository_full_name: str 
    local_repo_path: Optional[str] = None
    remote_url: Optional[str] = None
    default_branch: Optional[str] = None
    commit_sha: Optional[str] = None
    message: str

class InspectRepositoryResult(BaseModel):
    status: Literal["completed", "partial", "failed"]
    repository_tree: list[str] =  Field(default_factory=list)
    repository_tree_truncated: bool = False
    repository_guides: dict[str, str] = Field(default_factory=dict)
    project_manifests: dict[str, str] = Field(default_factory=dict)
    project_language: Optional[str] = None
    package_manager: Optional[str] = None
    python_version_constraint: Optional[str] = None
    test_framework: Optional[str] = None
    test_config_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: Optional[str] = None

class RepositoryStats(BaseModel):
    stars: int = Field(ge=0)
    forks: int = Field(ge=0)
    open_issues: int = Field(ge=0)
    default_branch: str = Field(min_length=1)
    last_pushed_at: str
    has_issues: bool
    license: str | None = None

class IssueCandidate(BaseModel):
    repository_full_name: str
    issue_number: int = Field(strict=True, gt=0)
    title: str = Field(min_length=1)
    url: str
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    updated_at: str
    repository_stats: RepositoryStats

class TriageDecision(BaseModel):
    status: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1)
    issue_key: Optional[str] = None

    @model_validator(mode="after")
    def validate_decision(self) -> TriageDecision:
        if not self.reason.strip():
            raise ValueError("La motivazione non può essere vuota")

        if self.status == "accepted":
            if not self.issue_key or not self.issue_key.strip():
                raise ValueError("Una decisione accepted richiede issue_key")

        elif self.issue_key is not None:
            raise ValueError("Una decisione rejected non seleziona issue")

        return self

class InvestigationFinding(BaseModel):
    file: Optional[str] = None
    line: int | None = Field(default=None, strict=True, gt=0)
    observation: str = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    confidence: float = Field(ge=0, le = 1)
    confidence_reason: str = Field(min_length=1)

class TestExecutionResult(BaseModel):
    """Execution metadata supplied by the sandboxed test runner."""

    command: str = Field(min_length=1)
    exit_code: int | None = Field(default=None, strict=True)
    timed_out: bool = False

    @model_validator(mode="after")
    def validate_execution(self) -> TestExecutionResult:
        if not self.command.strip():
            raise ValueError("Il comando dei test non può essere vuoto")
        if not self.timed_out and self.exit_code is None:
            raise ValueError("Un'esecuzione terminata richiede exit_code")
        return self


class SherpaReport(BaseModel):
    # Identità dell'issue e del checkout analizzato
    repository_full_name: str = Field(min_length=1)
    issue_number: int = Field(strict=True, gt=0)
    issue_url: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")

    # Problema e riproduzione
    problem_summary: str = Field(min_length=1)
    declared_reproduction_steps: list[str] = Field(default_factory=list)
    verified_reproduction_steps: list[str] = Field(default_factory=list)

    # Evidenze e conclusioni
    findings: list[InvestigationFinding] = Field(default_factory=list)
    investigation_status: Literal[
        "conclusive", "inconclusive"
    ]
    main_hypothesis: str | None = None
    alternative_hypotheses: list[str] = Field(default_factory=list)
    investigation_limits: list[str] = Field(default_factory=list)

    # Indicazioni per lo sviluppatore
    suggested_strategy: str | None = None
    tests_to_add: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    # Test effettivamente eseguiti
    test_status: Literal[
        "not_run", "passed", "failed", "timeout"
    ] = "not_run"
    test_result: TestExecutionResult | None = None

    @model_validator(mode="after")
    def validate_test_status(self) -> SherpaReport:
        if self.test_status == "not_run":
            if self.test_result is not None:
                raise ValueError("not_run non può contenere un risultato dei test")
            return self

        if self.test_result is None:
            raise ValueError("Lo stato dei test richiede un risultato del runner")

        if self.test_result.timed_out:
            expected_status = "timeout"
        elif self.test_result.exit_code == 0:
            expected_status = "passed"
        else:
            expected_status = "failed"

        if self.test_status != expected_status:
            raise ValueError("Lo stato dei test è incoerente con il risultato del runner")
        return self

    @model_validator(mode="after")
    def validate_conclusions(self) -> SherpaReport:
        if self.investigation_status == "conclusive":
            if not self.main_hypothesis or not self.main_hypothesis.strip():
                raise ValueError(
                    "Un'indagine conclusiva richiede un'ipotesi principale"
                )
            if not self.findings:
                raise ValueError(
                    "Un'indagine conclusiva richiede evidenze"
                )

        return self


@dataclass(frozen=True)
class CloneWorkerRequest:
    url: str
    branch: str
    destination: str
    depth: int


@dataclass(frozen=True)
class CloneWorkerResult:
    status: Literal["success", "git_error", "worker_error"]
    message: str = ""
    command: str | None = None
    stderr: str | None = None


@dataclass
class WorkerHandle:
    process: object
    result_queue: object
    job_handle: int | None = None
