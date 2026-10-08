"""Structured clone failures preserve compatibility without raw diagnostics."""
import pytest

from src.utils.config import Settings
from src.utils import repository as service


def test_invalid_clone_input_is_not_retryable(tmp_path):
    result = service.clone_repository_service("invalid", 0, "main", Settings(
        github_token="fake", google_api_key="fake", workspace_root=tmp_path,
    ))
    assert result.status == "failed"
    assert result.error.phase == "ingestion"
    assert result.error.category == "invalid_input"
    assert result.error.retryable is False
    assert result.message == result.error.message


def test_clone_timeout_has_a_safe_structured_diagnostic(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("token=TEST_ONLY_SECRET")
    monkeypatch.setattr(service, "clone_into_temporary_directory", timeout)
    result = service.clone_repository_service("owner/project", 42, "main", Settings(
        github_token="fake", google_api_key="fake", workspace_root=tmp_path,
    ))
    assert result.status == "failed"
    assert result.error.category == "operational"
    assert result.error.retryable is True
    assert "TEST_ONLY_SECRET" not in result.model_dump_json()


@pytest.mark.parametrize("field", ["repository_full_name", "issue_number", "default_branch"])
def test_invalid_clone_fields_are_classified_before_filesystem_work(tmp_path, field):
    data = {"repository_full_name": "owner/project", "issue_number": 42, "default_branch": "main"}
    data[field] = {"repository_full_name": "../secret", "issue_number": True, "default_branch": "--bad"}[field]
    result = service.clone_repository_service(**data, settings=Settings(
        github_token="fake", google_api_key="fake", workspace_root=tmp_path,
    ))
    assert result.error.category == "invalid_input"
    assert result.error.retryable is False
