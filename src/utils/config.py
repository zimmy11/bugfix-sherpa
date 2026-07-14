from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class Settings(BaseModel):
    # Credenziali
    github_token: str
    google_api_key: str

    # Modelli LLM
    discovery_model: str = "gemini-3.1-flash-lite"
    triage_model: str = "gemini-3.1-flash-lite"
    ingestion_model: str = "gemini-3.1-flash-lite"
    investigation_model: str = "gemini-3.1-pro"
    advisory_model: str = "gemini-3.1-pro"

    # Discovery
    github_queries: list[str] = None
    github_language: str = "python"
    github_labels: list[str] = Field(
        default_factory=lambda: [
            "good first issue",
            "help wanted",
        ]
    )
    github_max_results: int = 30
    github_min_stars: int = 100
    repository_inactivity_days: int = 7

    # Workspace locale
    workspace_root: Path = Path("./workspace")
    max_repository_tree_depth: int = 4
    max_file_chunk_lines: int = 200
    max_file_chunk_chars: int = 20_000
    max_search_results: int = 20

    # Resilienza
    api_max_retries: int = 3
    api_retry_min_seconds: int = 2
    api_retry_max_seconds: int = 30

    # Test sandboxati
    test_timeout_seconds: int = 300
    sandbox_network_enabled: bool = False

    # Osservabilità
    log_level: str = "INFO"
    langsmith_tracing: bool = False
    langsmith_project: str = "sherpa"


    @model_validator(mode = "after")
    def create_github_queries(self):
        if self.github_queries is not None:
            return self
        self.github_queries = [(
            "is:issue "
            "is:open "
            "no:assignee "
            f"language:{self.github_language} "
            f'label:"{label}" '
            '("AI agent" OR LLM OR langgraph OR crewai OR autogen) '
            "in:title,body"
        ) for label in self.github_labels]
        return self


    @field_validator("github_token", "google_api_key")
    @classmethod
    def validate_secrets(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("La variabile non può essere vuota")
        return value.strip()

    @field_validator("github_max_results")
    @classmethod
    def validate_max_results(cls, value: int) -> int:
        if not 1 <= value <= 100:
            raise ValueError(
                "github_max_results deve essere compreso tra 1 e 100"
            )
        return value

    @field_validator("github_labels")
    @classmethod
    def validate_github_labels(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("github_labels deve contenere almeno una label")
        return value

    @field_validator(
        "github_min_stars",
    )
    @classmethod
    def validate_github_minimums(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Le soglie GitHub non possono essere negative")
        return value

    @field_validator("workspace_root")
    @classmethod
    def normalize_workspace_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()



    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        allowed_levels = {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }

        normalized = value.upper()

        if normalized not in allowed_levels:
            raise ValueError(
                f"Livello di log non valido: {value}"
            )

        return normalized
    
def load_settings() -> Settings:
    load_dotenv()

    labels = [
        label.strip()
        for label in os.getenv(
            "GITHUB_LABELS",
            "good first issue,help wanted",
        ).split(",")
        if label.strip()
    ]

    return Settings(
        github_token=os.getenv("GITHUB_TOKEN", ""),
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),

        discovery_model=os.getenv(
            "DISCOVERY_MODEL",
            "gemini-3.1-flash-lite",
        ),
        triage_model=os.getenv(
            "TRIAGE_MODEL",
            "gemini-3.1-flash-lite",
        ),
        ingestion_model=os.getenv(
            "INGESTION_MODEL",
            "gemini-3.1-flash-lite",
        ),
        investigation_model=os.getenv(
            "INVESTIGATION_MODEL",
            "gemini-3.1-pro",
        ),
        advisory_model=os.getenv(
            "ADVISORY_MODEL",
            "gemini-3.1-pro",
        ),

        github_language=os.getenv(
            "GITHUB_LANGUAGE",
            "python",
        ),
        github_labels=labels,
        github_max_results=int(
            os.getenv("GITHUB_MAX_RESULTS", "10")
        ),
        github_min_stars=int(
            os.getenv("GITHUB_MIN_STARS", "100")
        ),
        repository_inactivity_days=int(
            os.getenv(
                "REPOSITORY_INACTIVITY_DAYS",
                "7",
            )
        ),

        workspace_root=Path(
            os.getenv("WORKSPACE_ROOT", "./workspace")
        ),

        api_max_retries=int(
            os.getenv("API_MAX_RETRIES", "3")
        ),
        api_retry_min_seconds=int(
            os.getenv("API_RETRY_MIN_SECONDS", "2")
        ),
        api_retry_max_seconds=int(
            os.getenv("API_RETRY_MAX_SECONDS", "30")
        ),

        test_timeout_seconds=int(
            os.getenv("TEST_TIMEOUT_SECONDS", "300")
        ),
        sandbox_network_enabled=os.getenv(
            "SANDBOX_NETWORK_ENABLED",
            "false",
        ).lower() == "true",

        log_level=os.getenv("LOG_LEVEL", "INFO"),
        langsmith_tracing=os.getenv(
            "LANGSMITH_TRACING",
            "false",
        ).lower() == "true",
        langsmith_project=os.getenv(
            "LANGSMITH_PROJECT",
            "bugfix-sherpa",
        ),
    )
