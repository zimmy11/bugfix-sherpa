"""LangChain tools for controlled repository ingestion."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from src.utils.config import Settings
from src.utils.repository import clone_repository_service


def build_repository_tools(settings: Settings) -> dict[str, BaseTool]:
    """Build repository tools with workspace and safety limits pre-bound."""

    @tool
    def clone_selected_repository(
        repository_full_name: str,
        issue_number: int,
        default_branch: str,
    ) -> dict[str, Any]:
        """Clone or safely reuse the repository selected during triage.

        Use this tool once during Ingestion. Pass exactly the repository full
        name, issue number, and default branch already present in the workflow
        state. The destination and Git options are derived from trusted
        settings; do not pass URLs, paths, credentials, or command options.
        The tool never pulls, resets, cleans, commits, pushes, or modifies an
        existing incompatible checkout.
        """
        result = clone_repository_service(
            repository_full_name=repository_full_name,
            issue_number=issue_number,
            default_branch=default_branch,
            settings=settings,
        )
        return result.model_dump(mode="json")

    return {
        "clone_selected_repository": clone_selected_repository,
    }
