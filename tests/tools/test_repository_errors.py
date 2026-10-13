"""Structured clone failures preserve compatibility without raw diagnostics."""
import pytest

from src.utils.config import Settings
from src.utils import repository as service
from src.tools import repository as repository_tools
from src.tools.repository import build_repository_tools


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


def test_repository_tool_binds_trusted_settings_and_exposes_no_operational_overrides(
    tmp_path, monkeypatch,
):
    settings = Settings(
        github_token="TRUSTED_GITHUB_TOKEN",
        google_api_key="TRUSTED_GOOGLE_API_KEY",
        workspace_root=tmp_path / "trusted-workspace",
        max_repository_file_bytes=2048,
    )
    calls = []

    class Result:
        def model_dump(self, mode):
            assert mode == "json"
            return {"status": "cloned"}

    def clone_service(**kwargs):
        calls.append(kwargs)
        return Result()

    monkeypatch.setattr(repository_tools, "clone_repository_service", clone_service)
    clone_tool = build_repository_tools(settings)["clone_selected_repository"]

    assert set(clone_tool.args_schema.model_fields) == {
        "repository_full_name", "issue_number", "default_branch",
    }
    assert clone_tool.invoke({
        "repository_full_name": "owner/project",
        "issue_number": 7,
        "default_branch": "main",
    }) == {"status": "cloned"}
    assert calls == [{
        "repository_full_name": "owner/project",
        "issue_number": 7,
        "default_branch": "main",
        "settings": settings,
    }]
