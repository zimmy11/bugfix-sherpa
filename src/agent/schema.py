from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal



class CloneRepositoryResult(BaseModel):
    status: Literal["cloned", "reused", "failed"] = None
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
    test_config_files: Optional[list[str]] = None
    warnings: list[str] = Field(default_factory=list)
    message: Optional[str] = None