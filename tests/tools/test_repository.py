from pathlib import Path
from types import SimpleNamespace

from git import Actor, Repo

from src.utils import repository as repository_utils
from src.utils.repository_safety import (
    RepositoryValidationError,
    validate_branch,
    validate_issue_number,
    validate_repository_full_name,
)


def _source_repository(tmp_path):
    source = tmp_path / "source"
    repository = Repo.init(source, initial_branch="main")
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    repository.index.add(["README.md"])
    actor = Actor("Bugfix Sherpa", "sherpa@example.invalid")
    repository.index.commit("Initial commit", author=actor, committer=actor)
    return source, repository.head.commit.hexsha


def _settings(tmp_path):
    return SimpleNamespace(
        workspace_root=tmp_path / "workspace",
        reuse_existing_clone=True,
        repository_clone_depth=1,
        repository_clone_timeout_seconds=30,
    )


def test_clone_then_reuse_local_fixture(tmp_path, monkeypatch):
    source, expected_sha = _source_repository(tmp_path)
    monkeypatch.setattr(
        repository_utils,
        "build_public_clone_url",
        lambda owner, repository: str(source),
    )
    settings = _settings(tmp_path)

    cloned = repository_utils.clone_repository_service(
        "owner/project",
        42,
        "main",
        settings,
    )
    reused = repository_utils.clone_repository_service(
        "owner/project",
        42,
        "main",
        settings,
    )

    assert cloned.status == "cloned"
    assert cloned.commit_sha == expected_sha
    assert reused.status == "reused"
    assert reused.local_repo_path == cloned.local_repo_path
    assert reused.commit_sha == cloned.commit_sha


def test_existing_dirty_checkout_is_not_modified(tmp_path, monkeypatch):
    source, _ = _source_repository(tmp_path)
    monkeypatch.setattr(
        repository_utils,
        "build_public_clone_url",
        lambda owner, repository: str(source),
    )
    settings = _settings(tmp_path)
    cloned = repository_utils.clone_repository_service(
        "owner/project",
        7,
        "main",
        settings,
    )
    assert cloned.local_repo_path is not None
    checkout = Path(cloned.local_repo_path)
    marker = checkout / "user-work.txt"
    marker.write_text("do not delete\n", encoding="utf-8")

    result = repository_utils.clone_repository_service(
        "owner/project",
        7,
        "main",
        settings,
    )

    assert result.status == "failed"
    assert marker.read_text(encoding="utf-8") == "do not delete\n"


def test_repository_inputs_are_strictly_validated():
    assert validate_repository_full_name("owner/project") == (
        "owner",
        "project",
    )
    assert validate_issue_number(1) == 1
    assert validate_branch("release/1.0") == "release/1.0"

    for invalid_issue_number in (True, 0, -1, "1"):
        try:
            validate_issue_number(invalid_issue_number)
        except RepositoryValidationError:
            pass
        else:
            raise AssertionError("issue_number non valido accettato")
