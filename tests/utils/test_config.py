"""Validation of positive operational limits in Settings."""

import pytest
from pydantic import ValidationError

from src.utils import config
from src.utils.config import Settings


POSITIVE_LIMITS = (
    "repository_clone_depth",
    "repository_clone_timeout_seconds",
    "max_repository_tree_entries",
    "max_repository_tree_depth",
    "max_repository_file_bytes",
    "max_guide_chars",
    "max_ingestion_total_chars",
    "max_file_chunk_lines",
    "max_file_chunk_chars",
    "max_search_results",
    "api_max_retries",
    "api_retry_min_seconds",
    "api_retry_max_seconds",
    "test_timeout_seconds",
    "repository_inactivity_days",
)

ENV_LIMITS = (
    ("REPOSITORY_CLONE_DEPTH", "repository_clone_depth"),
    ("REPOSITORY_CLONE_TIMEOUT_SECONDS", "repository_clone_timeout_seconds"),
    ("MAX_REPOSITORY_TREE_DEPTH", "max_repository_tree_depth"),
    ("MAX_REPOSITORY_TREE_ENTRIES", "max_repository_tree_entries"),
    ("MAX_REPOSITORY_FILE_BYTES", "max_repository_file_bytes"),
    ("MAX_GUIDE_CHARS", "max_guide_chars"),
    ("MAX_INGESTION_TOTAL_CHARS", "max_ingestion_total_chars"),
    ("MAX_FILE_CHUNK_LINES", "max_file_chunk_lines"),
    ("MAX_FILE_CHUNK_CHARS", "max_file_chunk_chars"),
    ("MAX_SEARCH_RESULTS", "max_search_results"),
)


@pytest.fixture
def clean_config_env(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake")
    for env_name in (
        "GITHUB_LABELS",
        "GITHUB_MAX_RESULTS",
        "REPOSITORY_INACTIVITY_DAYS",
        "API_MAX_RETRIES",
        "API_RETRY_MIN_SECONDS",
        "API_RETRY_MAX_SECONDS",
        "TEST_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, _ in ENV_LIMITS:
        monkeypatch.delenv(env_name, raising=False)


@pytest.mark.parametrize("field", POSITIVE_LIMITS)
@pytest.mark.parametrize("value", (0, -1))
def test_positive_limits_reject_zero_and_negative_values(field, value):
    with pytest.raises(ValidationError) as exc_info:
        Settings(github_token="fake", google_api_key="fake", **{field: value})

    assert any(error["loc"] == (field,) for error in exc_info.value.errors())


@pytest.mark.parametrize("field", POSITIVE_LIMITS)
def test_positive_limits_accept_one(field):
    overrides = {field: 1}
    if field == "api_retry_max_seconds":
        overrides["api_retry_min_seconds"] = 1

    settings = Settings(github_token="fake", google_api_key="fake", **overrides)

    assert getattr(settings, field) == 1


@pytest.mark.parametrize("env_name, field", ENV_LIMITS)
def test_load_settings_reads_configured_limit(clean_config_env, monkeypatch, env_name, field):
    monkeypatch.setenv(env_name, "2")

    settings = config.load_settings()

    assert getattr(settings, field) == 2


@pytest.mark.parametrize("env_name, field", ENV_LIMITS)
@pytest.mark.parametrize("value", ("0", "-1"))
def test_load_settings_rejects_invalid_limit(
    clean_config_env, monkeypatch, env_name, field, value,
):
    monkeypatch.setenv(env_name, value)

    with pytest.raises(ValidationError) as exc_info:
        config.load_settings()

    assert any(error["loc"] == (field,) for error in exc_info.value.errors())


def test_load_settings_uses_model_limit_defaults(clean_config_env):
    settings = config.load_settings()

    for _, field in ENV_LIMITS:
        assert getattr(settings, field) == Settings.model_fields[field].default
    assert settings.github_max_results == Settings.model_fields["github_max_results"].default


def test_backoff_rejects_minimum_greater_than_maximum():
    with pytest.raises(ValidationError, match="api_retry_min_seconds"):
        Settings(
            github_token="fake",
            google_api_key="fake",
            api_retry_min_seconds=4,
            api_retry_max_seconds=3,
        )


def test_backoff_accepts_equal_bounds():
    settings = Settings(
        github_token="fake",
        google_api_key="fake",
        api_retry_min_seconds=3,
        api_retry_max_seconds=3,
    )

    assert settings.api_retry_min_seconds == settings.api_retry_max_seconds


@pytest.mark.parametrize("queries", ([], [""], ["   "]))
def test_explicit_empty_queries_are_rejected(queries):
    with pytest.raises(ValidationError):
        Settings(github_token="fake", google_api_key="fake", github_queries=queries)


def test_explicit_queries_are_trimmed():
    settings = Settings(
        github_token="fake",
        google_api_key="fake",
        github_queries=["  is:issue bug  "],
    )

    assert settings.github_queries == ["is:issue bug"]


def test_default_queries_are_generated():
    settings = Settings(github_token="fake", google_api_key="fake")

    assert settings.github_queries
    assert all(query.strip() for query in settings.github_queries)


def test_load_settings_rejects_inverted_backoff(clean_config_env, monkeypatch):
    monkeypatch.setenv("API_RETRY_MIN_SECONDS", "5")
    monkeypatch.setenv("API_RETRY_MAX_SECONDS", "4")

    with pytest.raises(ValidationError, match="api_retry_min_seconds"):
        config.load_settings()
