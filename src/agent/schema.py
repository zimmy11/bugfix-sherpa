from __future__ import annotations
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Optional, Literal
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json

from src.utils.repository_safety import validate_branch
from src.utils.error_safety import sanitize_error_message

INSPECTION_SECTIONS = frozenset({
    "tree", "guides", "manifests", "test_config"
})


def validate_relative_snapshot_path(path: str) -> str:
    """Require a canonical, relative POSIX path in an inspection snapshot."""
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        raise ValueError(f"Path relativo dello snapshot non valido: {path!r}")
    return path


class CloneRepositoryResult(BaseModel):
    status: Literal["cloned", "reused", "failed"]
    repository_full_name: str
    local_repo_path: Optional[str] = None
    remote_url: Optional[str] = None
    default_branch: Optional[str] = None
    commit_sha: Optional[str] = None
    message: Annotated[str, AfterValidator(sanitize_error_message)]
    error: WorkflowError | None = None

class InspectRepositoryResult(BaseModel):
    status: Literal["completed", "partial", "failed"]
    repository_tree: list[str] =  Field(default_factory=list)
    repository_tree_truncated: bool = False
    truncated_files: list[str] = Field(default_factory=list)
    repository_path: Optional[str] = None
    inspected_commit_sha: Optional[str] = Field(
        default=None, pattern=r"^[0-9a-fA-F]{40}$"
    )
    inspected_branch: Optional[str] = None
    repository_guides: dict[str, str] = Field(default_factory=dict)
    project_manifests: dict[str, str] = Field(default_factory=dict)
    project_language: Optional[str] = None
    package_manager: Optional[str] = None
    python_version_constraint: Optional[str] = None
    test_framework: Optional[str] = None
    test_config_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    inspected_sections: list[Literal["tree", "guides", "manifests", "test_config"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_inspected_fields(self) -> InspectRepositoryResult:
        sections = set(self.inspected_sections)
        if len(sections) != len(self.inspected_sections):
            raise ValueError("Ci sono sezioni duplicate tra quelle ispezionate")
        if len(set(self.repository_tree)) != len(self.repository_tree):
            raise ValueError("L'albero contiene path duplicati")
        if len(set(self.test_config_files)) != len(self.test_config_files):
            raise ValueError("I file di configurazione dei test sono duplicati")
        if len(set(self.truncated_files)) != len(self.truncated_files):
            raise ValueError("I file troncati sono duplicati")
        for path in (
            *self.repository_tree,
            *self.repository_guides,
            *self.project_manifests,
            *self.test_config_files,
            *self.truncated_files,
        ):
            validate_relative_snapshot_path(path)
        for section, has_data in (
            ("tree", bool(self.repository_tree)),
            ("guides", bool(self.repository_guides)),
            ("manifests", bool(self.project_manifests)),
            ("test_config", bool(self.test_config_files)),
        ):
            if has_data and section not in sections:
                raise ValueError(f"Dati presenti per sezione non ispezionata: {section}")
        if not set(self.truncated_files).issubset(
            self.repository_guides.keys() | self.project_manifests.keys()
        ):
            raise ValueError("Un file troncato non è presente nello snapshot")
        warnings_present = any(warning.strip() for warning in self.warnings)
        if self.repository_tree_truncated:
            if "tree" not in sections or not warnings_present:
                raise ValueError("Albero troncato senza scansione e warning")
        if self.truncated_files and not warnings_present:
            raise ValueError("File troncati senza warning")
        if self.status == "failed":
            return self

        if self.status in ("partial", "completed"):
            if not self.repository_path or not self.repository_path.strip():
                raise ValueError("L'ispezione richiede il path del checkout")
            if not Path(self.repository_path).is_absolute():
                raise ValueError("Il path ispezionato deve essere assoluto")
            if not self.inspected_commit_sha:
                raise ValueError("L'ispezione richiede lo SHA del checkout")
            if not self.inspected_branch:
                raise ValueError("L'ispezione richiede il branch del checkout")
            validate_branch(self.inspected_branch)
        if self.status == "partial":
            if not sections:
                raise ValueError("Status è partial ma la lista delle sezioni ispezionate è vuota")
            if not warnings_present:
                raise ValueError("Status è partial ma la lista dei warnings è vuota")
        if self.status == "completed":
            if sections != INSPECTION_SECTIONS:
                raise ValueError("Ispezione completed senza tutte le sezioni")
            if self.repository_tree_truncated or self.truncated_files:
                raise ValueError("Ispezione completed non può essere troncata")

        return self

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


def validate_nonblank_text(value: str) -> str:
    """Validate content without changing log excerpts or other supplied text."""
    if not value.strip():
        raise ValueError("Il testo non può essere vuoto o contenere solo spazi")
    return value


NonBlankText = Annotated[
    str, Field(min_length=1), AfterValidator(validate_nonblank_text)
]
MAX_LOG_EXCERPT_CHARS = 2000


def validate_evidence_path(value: str) -> str:
    """Validate path shape only; checkout/file/line verification is separate."""
    validate_relative_snapshot_path(value)
    if any(not part.strip() for part in value.split("/")):
        raise ValueError("Il path dell'evidenza contiene una componente vuota")
    return value


class CodeFindingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["code"]
    file: Annotated[NonBlankText, AfterValidator(validate_evidence_path)]
    line: int = Field(strict=True, gt=0)


class LogFindingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["log"]
    execution_id: NonBlankText
    excerpt: Annotated[NonBlankText, Field(max_length=MAX_LOG_EXCERPT_CHARS)]


class InvestigationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[
        CodeFindingSource | LogFindingSource, Field(discriminator="type")
    ]
    observation: NonBlankText
    interpretation: NonBlankText
    confidence: float = Field(ge=0, le=1)
    confidence_reason: NonBlankText


class TestExecutionResult(BaseModel):
    """Execution metadata and logs supplied by the sandboxed test runner."""

    command: NonBlankText
    exit_code: int | None = Field(default=None, strict=True)
    timed_out: bool = Field(default=False, strict=True)
    logs: str | None = None

    @model_validator(mode="after")
    def validate_execution(self) -> TestExecutionResult:
        if not self.timed_out and self.exit_code is None:
            raise ValueError("Un'esecuzione terminata richiede exit_code")
        return self

    @property
    def status(self) -> Literal["passed", "failed", "timeout"]:
        if self.timed_out:
            return "timeout"
        return "passed" if self.exit_code == 0 else "failed"

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class SherpaReport(BaseModel):
    # Identità dell'issue e del checkout analizzato
    repository_full_name: NonBlankText
    issue_number: int = Field(strict=True, gt=0)
    issue_url: NonBlankText
    branch: NonBlankText
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")

    # Problema e riproduzione
    problem_summary: NonBlankText
    declared_reproduction_steps: list[NonBlankText] = Field(default_factory=list)
    verified_reproduction_steps: list[NonBlankText] = Field(default_factory=list)

    # Evidenze e conclusioni
    findings: list[InvestigationFinding] = Field(default_factory=list)
    investigation_status: Literal[
        "conclusive", "inconclusive"
    ]
    main_hypothesis: NonBlankText | None = None
    conclusion_reason: NonBlankText | None = None
    alternative_hypotheses: list[NonBlankText] = Field(default_factory=list)
    investigation_limits: list[NonBlankText] = Field(default_factory=list)

    # Indicazioni per lo sviluppatore
    suggested_strategy: NonBlankText | None = None
    tests_to_add: list[NonBlankText] = Field(default_factory=list)
    risks: list[NonBlankText] = Field(default_factory=list)
    open_questions: list[NonBlankText] = Field(default_factory=list)

    # Test effettivamente eseguiti
    test_status: Literal[
        "not_run", "passed", "failed", "timeout"
    ] = "not_run"
    test_result: TestExecutionResult | None = None

    @property
    def report_id(self) -> str:
        """Content fingerprint: a changed report requires a new review."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def validate_test_status(self) -> SherpaReport:
        if self.test_status == "not_run":
            if self.test_result is not None:
                raise ValueError("not_run non può contenere un risultato dei test")
            return self

        if self.test_result is None:
            raise ValueError("Lo stato dei test richiede un risultato del runner")

        if self.test_status != self.test_result.status:
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
            if self.conclusion_reason is None:
                raise ValueError(
                    "Un'indagine conclusiva richiede una motivazione complessiva"
                )

        return self

WorkflowPhase = Literal["triage", "investigation", "ingestion", "discovery", "advisory", "human_review"]


class WorkflowError(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    phase: WorkflowPhase
    category: Literal[
        "operational", "invalid_input", "invariant_violation",
    ]
    message: Annotated[NonBlankText, AfterValidator(sanitize_error_message)]
    retryable: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_inconsistent_error(self) -> WorkflowError:
        if self.category in ("invalid_input", "invariant_violation") and self.retryable:
            raise ValueError("Non è possibile avere un errore del WorkFlow retryable appartenente alla categoria 'invalid_input' o 'invariant_violation'")
        return self



class DiscoveryOutcome(BaseModel):
    """Validated final search outcome; pending remains a workflow state."""

    status: Literal["completed", "no_results", "failed"]
    candidates: list[IssueCandidate] = Field(default_factory=list)
    error: WorkflowError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> DiscoveryOutcome:
        if self.status == "failed":
            if self.error is None or self.error.phase != "discovery":
                raise ValueError("Discovery fallita richiede un errore Discovery")
        else:
            if self.error is not None:
                raise ValueError("Discovery riuscita non puo avere un errore")
            if self.status == "completed" and not self.candidates:
                raise ValueError("Discovery completata richiede candidate")
            if self.status == "no_results" and self.candidates:
                raise ValueError("no_results richiede candidate vuote")
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


# Resolve the error type defined after the clone result.
CloneRepositoryResult.model_rebuild()
