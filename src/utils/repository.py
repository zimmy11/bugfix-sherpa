from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from git import Repo
from git.exc import GitCommandError as GitPythonCommandError

from src.agent.schema import CloneRepositoryResult
from src.utils.config import Settings
from src.utils.repository_safety import (
    RepositoryValidationError,
    build_public_clone_url,
    build_repository_workspace,
    resolve_inside_workspace,
    sanitize_remote_url,
    validate_branch,
    validate_existing_checkout,
    validate_issue_number,
    validate_repository_full_name,
)


_TEMP_DIRECTORY_PREFIX = ".bugfix-sherpa-clone-"


@dataclass(frozen=True)
class CheckoutMetadata:
    """Internal, immutable view of a local Git checkout."""

    path: Path
    remote_url: str
    branch: str
    commit_sha: str
    is_dirty: bool
    has_in_progress_operation: bool


def _has_in_progress_operation(repository: Repo) -> bool:
    git_dir = Path(repository.git_dir)
    markers = (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-apply",
        "rebase-merge",
    )
    return any((git_dir / marker).exists() for marker in markers)


def read_checkout_metadata(repo_path: str | Path) -> CheckoutMetadata:
    """Read the metadata required to verify or reuse a checkout."""
    path = Path(repo_path).expanduser().resolve(strict=True)
    repository = Repo(path)

    if repository.bare or repository.working_tree_dir is None:
        raise RepositoryValidationError(
            f"Il checkout non è un worktree Git valido: {path}"
        )

    try:
        origin = repository.remotes.origin
        remote_url = next(iter(origin.urls))
    except (AttributeError, StopIteration) as exc:
        raise RepositoryValidationError(
            f"Il checkout non possiede un remote origin valido: {path}"
        ) from exc

    if repository.head.is_detached:
        raise RepositoryValidationError(
            f"Il checkout è in detached HEAD: {path}"
        )

    return CheckoutMetadata(
        path=path,
        remote_url=sanitize_remote_url(remote_url),
        branch=repository.active_branch.name,
        commit_sha=repository.head.commit.hexsha,
        is_dirty=repository.is_dirty(untracked_files=True),
        has_in_progress_operation=_has_in_progress_operation(repository),
    )


def can_reuse_checkout(
    repo_path: str | Path,
    expected_remote: str,
    expected_branch: str,
) -> bool:
    """Return whether an existing checkout is safe to reuse unchanged."""
    try:
        validated_path = validate_existing_checkout(
            repo_path,
            expected_remote,
        )
        metadata = read_checkout_metadata(validated_path)
    except (RepositoryValidationError, GitPythonCommandError, OSError):
        return False

    return (
        metadata.branch == expected_branch
        and not metadata.is_dirty
        and not metadata.has_in_progress_operation
    )


def clone_into_temporary_directory(
    url: str,
    temp_path: str | Path,
    branch: str,
    depth: int,
    timeout_seconds: int,
) -> Repo:
    """Clone one branch into an empty, caller-owned temporary directory."""
    destination = Path(temp_path)
    if not destination.is_dir() or any(destination.iterdir()):
        raise RepositoryValidationError(
            "La directory temporanea del clone deve esistere ed essere vuota"
        )
    if depth <= 0 or timeout_seconds <= 0:
        raise RepositoryValidationError(
            "Profondità e timeout del clone devono essere positivi"
        )

    clone_options: dict[str, object] = {
        "branch": branch,
        "depth": depth,
        "single_branch": True,
        "recurse_submodules": False,
    }
    # GitPython cannot enforce kill_after_timeout on Windows. The temporary
    # checkout still prevents an interrupted clone from corrupting the final
    # destination; a process-level sandbox can enforce the hard Windows limit.
    if os.name != "nt":
        clone_options["kill_after_timeout"] = timeout_seconds

    return Repo.clone_from(
        url,
        destination,
        env={"GIT_TERMINAL_PROMPT": "0"},
        allow_unsafe_protocols=False,
        allow_unsafe_options=False,
        **clone_options,
    )


def _create_temporary_directory(parent: Path) -> Path:
    return Path(
        tempfile.mkdtemp(
            prefix=_TEMP_DIRECTORY_PREFIX,
            dir=parent,
        )
    )


def _cleanup_owned_temporary_directory(
    temp_path: Path | None,
    expected_parent: Path,
) -> None:
    if temp_path is None or not temp_path.exists():
        return

    try:
        resolved_parent = expected_parent.resolve(strict=True)
        lexical_temp = Path(os.path.abspath(temp_path))
        if (
            lexical_temp.parent != resolved_parent
            or not lexical_temp.name.startswith(_TEMP_DIRECTORY_PREFIX)
            or lexical_temp.is_symlink()
        ):
            return
    except OSError:
        return

    shutil.rmtree(lexical_temp)


def _verified_metadata(
    path: Path,
    expected_remote: str,
    expected_branch: str,
) -> CheckoutMetadata:
    validated_path = validate_existing_checkout(path, expected_remote)
    metadata = read_checkout_metadata(validated_path)
    if metadata.branch != expected_branch:
        raise RepositoryValidationError(
            "Il branch del checkout non corrisponde al default branch atteso"
        )
    if metadata.has_in_progress_operation:
        raise RepositoryValidationError(
            "Il checkout contiene un'operazione Git incompleta"
        )
    return metadata


def _result_from_metadata(
    status: Literal["cloned", "reused"],
    repository_full_name: str,
    metadata: CheckoutMetadata,
    message: str,
) -> CloneRepositoryResult:
    return CloneRepositoryResult(
        status=status,
        repository_full_name=repository_full_name,
        local_repo_path=str(metadata.path),
        remote_url=metadata.remote_url,
        default_branch=metadata.branch,
        commit_sha=metadata.commit_sha,
        message=message,
    )


def clone_repository_service(
    repository_full_name: str,
    issue_number: int,
    default_branch: str,
    settings: Settings,
) -> CloneRepositoryResult:
    """Clone or safely reuse the selected repository checkout.

    The final destination is never modified when it already exists. New
    checkouts are cloned and verified in a caller-owned temporary directory,
    then atomically renamed into place on the same filesystem.
    """
    safe_repository_name = (
        repository_full_name
        if isinstance(repository_full_name, str)
        else "<invalid>"
    )
    temp_path: Path | None = None
    destination_parent: Path | None = None

    try:
        owner, repository_name = validate_repository_full_name(
            repository_full_name
        )
        validated_issue_number = validate_issue_number(issue_number)
        validated_branch = validate_branch(default_branch)

        workspace_root = Path(settings.workspace_root).expanduser().resolve()
        destination = resolve_inside_workspace(
            workspace_root,
            build_repository_workspace(
                workspace_root,
                owner,
                repository_name,
                validated_issue_number,
            ),
        )
        expected_remote = build_public_clone_url(owner, repository_name)

        if destination.exists():
            if not getattr(settings, "reuse_existing_clone", True):
                raise RepositoryValidationError(
                    "La destinazione esiste e il riuso dei clone è disabilitato"
                )
            if not can_reuse_checkout(
                destination,
                expected_remote,
                validated_branch,
            ):
                raise RepositoryValidationError(
                    "La destinazione esiste ma non è un clone pulito e "
                    "compatibile; non è stata modificata"
                )
            metadata = _verified_metadata(
                destination,
                expected_remote,
                validated_branch,
            )
            return _result_from_metadata(
                "reused",
                repository_full_name,
                metadata,
                "Clone locale valido riutilizzato senza modificarlo.",
            )

        destination_parent = resolve_inside_workspace(
            workspace_root,
            destination.parent,
        )
        destination_parent.mkdir(parents=True, exist_ok=True)
        destination_parent = destination_parent.resolve(strict=True)
        temp_path = _create_temporary_directory(destination_parent)
        temp_path = resolve_inside_workspace(workspace_root, temp_path)

        clone_into_temporary_directory(
            expected_remote,
            temp_path,
            validated_branch,
            getattr(settings, "repository_clone_depth", 1),
            getattr(settings, "repository_clone_timeout_seconds", 120),
        )
        _verified_metadata(temp_path, expected_remote, validated_branch)

        if destination.exists():
            if can_reuse_checkout(
                destination,
                expected_remote,
                validated_branch,
            ):
                metadata = _verified_metadata(
                    destination,
                    expected_remote,
                    validated_branch,
                )
                return _result_from_metadata(
                    "reused",
                    repository_full_name,
                    metadata,
                    "Un clone concorrente valido è stato riutilizzato.",
                )
            raise RepositoryValidationError(
                "La destinazione è comparsa durante il clone e non è "
                "riutilizzabile; non è stata modificata"
            )

        temp_path.replace(destination)
        temp_path = None
        metadata = _verified_metadata(
            destination,
            expected_remote,
            validated_branch,
        )
        return _result_from_metadata(
            "cloned",
            repository_full_name,
            metadata,
            "Repository clonata e verificata correttamente.",
        )
    except (
        AttributeError,
        GitPythonCommandError,
        OSError,
        RepositoryValidationError,
        TypeError,
        ValueError,
    ) as exc:
        return CloneRepositoryResult(
            status="failed",
            repository_full_name=safe_repository_name,
            message=f"Clone non completato: {exc}",
        )
    finally:
        if destination_parent is not None:
            _cleanup_owned_temporary_directory(
                temp_path,
                destination_parent,
            )

