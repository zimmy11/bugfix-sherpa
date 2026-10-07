"""Checkout and inspection SHA must identify the same commit."""

import pytest

from src.agent.router import route_after_ingestion
from src.agent.schema import (
    CloneRepositoryResult,
    InspectRepositoryResult,
    IssueCandidate,
    RepositoryStats,
)
from src.agent.state import (
    BugFixingState,
    clone_checkout_update,
    inspection_checkout_update,
    validate_ingestion_snapshot,
)
from src.utils.config import Settings


@pytest.fixture
def valid_snapshot(tmp_path):
    candidate = IssueCandidate(
        repository_full_name="owner/project",
        issue_number=42,
        title="Example bug",
        url="https://github.com/owner/project/issues/42",
        updated_at="2026-01-01T00:00:00+00:00",
        repository_stats=RepositoryStats(
            stars=100,
            forks=1,
            open_issues=1,
            default_branch="main",
            last_pushed_at="2026-01-01T00:00:00+00:00",
            has_issues=True,
        ),
    )
    checkout_path = tmp_path / "workspace" / "owner" / "project" / "issue-42"
    state = BugFixingState(
        issue_candidates=[candidate],
        selected_issue=candidate,
        issue_number=42,
        issue_url=candidate.url,
        repository_full_name=candidate.repository_full_name,
        default_branch="main",
        triage_status="accepted",
        ingestion_status="completed",
        inspection_status="completed",
        inspected_sections=["tree", "guides", "manifests", "test_config"],
        local_repo_path=str(checkout_path),
        inspected_repo_path=str(checkout_path),
        repository_remote_url="https://github.com/owner/project.git",
        repository_branch="main",
        inspected_branch="main",
        repository_revision="a" * 40,
        inspected_commit_sha="a" * 40,
    )
    settings = Settings(
        github_token="fake",
        google_api_key="fake",
        workspace_root=tmp_path / "workspace",
    )
    return state, settings


@pytest.mark.parametrize("value", (None, "", "abc", "g" * 40))
def test_invalid_clone_sha_is_rejected(valid_snapshot, value):
    state, settings = valid_snapshot
    state.repository_revision = value

    with pytest.raises(ValueError, match="SHA del checkout mancante o non valido"):
        validate_ingestion_snapshot(state, settings)


@pytest.mark.parametrize("value", (None, "", "abc", "g" * 40))
def test_invalid_inspection_sha_is_rejected(valid_snapshot, value):
    state, settings = valid_snapshot
    state.inspected_commit_sha = value

    with pytest.raises(ValueError, match="SHA ispezionato mancante o non valido"):
        validate_ingestion_snapshot(state, settings)


def test_inspection_sha_must_match_clone(valid_snapshot):
    state, settings = valid_snapshot
    state.inspected_commit_sha = "b" * 40

    with pytest.raises(ValueError, match="SHA ispezionato diverso"):
        validate_ingestion_snapshot(state, settings)


def test_matching_sha_accepts_hex_case_difference(valid_snapshot):
    state, settings = valid_snapshot
    state.inspected_commit_sha = "A" * 40

    validate_ingestion_snapshot(state, settings)


@pytest.mark.parametrize("status", ("not_started", "failed"))
def test_incomplete_inspection_is_rejected_even_with_metadata(valid_snapshot, status):
    state, settings = valid_snapshot
    state.inspection_status = status

    with pytest.raises(ValueError, match="Ispezione del checkout non completata"):
        validate_ingestion_snapshot(state, settings)


def test_completed_inspection_accepts_empty_test_config_list(valid_snapshot):
    state, settings = valid_snapshot
    assert state.test_config_files == []

    assert route_after_ingestion(state, settings=settings) == "completed"


@pytest.mark.parametrize(
    ("field", "error"),
    [
        ("inspected_repo_path", "Path del checkout ispezionato mancante"),
        ("repository_branch", "Branch del clone mancante"),
        ("inspected_branch", "Branch ispezionato mancante"),
    ],
)
def test_missing_inspection_identity_is_rejected(valid_snapshot, field, error):
    state, settings = valid_snapshot
    setattr(state, field, None)

    with pytest.raises(ValueError, match=error):
        validate_ingestion_snapshot(state, settings)


def test_remote_must_match_public_clone_url(valid_snapshot):
    state, settings = valid_snapshot
    state.repository_remote_url = "https://evil.example/owner/project.git"

    with pytest.raises(ValueError, match="URL pubblico atteso"):
        validate_ingestion_snapshot(state, settings)


@pytest.mark.parametrize("field", ("local_repo_path", "inspected_repo_path"))
def test_checkout_paths_must_stay_inside_workspace(valid_snapshot, tmp_path, field):
    state, settings = valid_snapshot
    setattr(state, field, str(tmp_path / "outside" / "issue-42"))

    with pytest.raises(ValueError, match="outside workspace"):
        validate_ingestion_snapshot(state, settings)


def test_clone_and_inspection_paths_must_match(valid_snapshot):
    state, settings = valid_snapshot
    state.inspected_repo_path = str(settings.workspace_root / "other" / "issue-42")

    with pytest.raises(ValueError, match="Path locale non coerente"):
        validate_ingestion_snapshot(state, settings)


def test_checkout_must_belong_to_selected_issue(valid_snapshot):
    state, settings = valid_snapshot
    wrong_path = str(settings.workspace_root / "owner" / "project" / "issue-99")
    state.local_repo_path = wrong_path
    state.inspected_repo_path = wrong_path

    with pytest.raises(ValueError, match="issue selezionata"):
        validate_ingestion_snapshot(state, settings)


@pytest.mark.parametrize("field", ("repository_branch", "inspected_branch"))
def test_branch_must_match_selected_issue(valid_snapshot, field):
    state, settings = valid_snapshot
    setattr(state, field, "other")

    with pytest.raises(ValueError, match="Branch Ispezionato diverso"):
        validate_ingestion_snapshot(state, settings)


def test_clone_result_maps_to_state_fields(valid_snapshot):
    state, _ = valid_snapshot
    result = CloneRepositoryResult(
        status="cloned",
        repository_full_name="owner/project",
        local_repo_path=state.local_repo_path,
        remote_url=state.repository_remote_url,
        default_branch="main",
        commit_sha="a" * 40,
        message="Cloned",
    )

    assert clone_checkout_update(result) == {
        "local_repo_path": state.local_repo_path,
        "repository_remote_url": state.repository_remote_url,
        "repository_branch": "main",
        "repository_revision": "a" * 40,
    }


def test_inspection_result_maps_to_state_fields(valid_snapshot):
    state, _ = valid_snapshot
    result = InspectRepositoryResult(
        status="completed",
        repository_path=state.local_repo_path,
        inspected_commit_sha="a" * 40,
        inspected_branch="main",
        inspected_sections=["tree", "guides", "manifests", "test_config"],
        warnings=["README missing"],
    )

    update = inspection_checkout_update(result)

    assert update["inspection_status"] == "completed"
    assert update["inspected_repo_path"] == state.local_repo_path
    assert update["inspected_commit_sha"] == "a" * 40
    assert update["inspected_branch"] == "main"
    assert update["ingestion_warnings"] == ["README missing"]
    assert update["inspected_sections"] == [
        "tree", "guides", "manifests", "test_config"
    ]
    assert update["test_config_files"] == []


def test_failed_inspection_does_not_overwrite_clone_identity():
    result = InspectRepositoryResult(status="failed", warnings=["Read failed"])

    assert inspection_checkout_update(result) == {
        "inspection_status": "failed",
        "ingestion_warnings": ["Read failed"],
        "inspected_sections": [],
    }


def test_clone_and_inspection_results_form_a_valid_snapshot(valid_snapshot):
    state, settings = valid_snapshot
    state.local_repo_path = None
    state.repository_remote_url = None
    state.repository_branch = None
    state.repository_revision = None
    state.inspection_status = "not_started"
    state.inspected_repo_path = None
    state.inspected_commit_sha = None
    state.inspected_branch = None
    state.inspected_sections = []
    checkout_path = str(
        settings.workspace_root / "owner" / "project" / "issue-42"
    )
    clone = CloneRepositoryResult(
        status="cloned",
        repository_full_name="owner/project",
        local_repo_path=checkout_path,
        remote_url="https://github.com/owner/project.git",
        default_branch="main",
        commit_sha="a" * 40,
        message="Cloned",
    )
    inspection = InspectRepositoryResult(
        status="completed",
        repository_path=checkout_path,
        inspected_commit_sha="a" * 40,
        inspected_branch="main",
        inspected_sections=["tree", "guides", "manifests", "test_config"],
    )

    for field, value in clone_checkout_update(clone).items():
        setattr(state, field, value)
    for field, value in inspection_checkout_update(inspection).items():
        setattr(state, field, value)

    assert route_after_ingestion(state, settings=settings) == "completed"


def test_partial_snapshot_with_empty_scanned_tree_and_warning_is_usable(
    valid_snapshot,
):
    state, settings = valid_snapshot
    state.inspection_status = "partial"
    state.inspected_sections = ["tree"]
    state.ingestion_warnings = ["Guide non esaminate"]

    assert state.repository_tree == []
    assert route_after_ingestion(state, settings=settings) == "completed"


@pytest.mark.parametrize("field", ("inspected_sections", "ingestion_warnings"))
def test_partial_snapshot_missing_coverage_or_warning_is_rejected(
    valid_snapshot, field,
):
    state, settings = valid_snapshot
    state.inspection_status = "partial"
    state.inspected_sections = ["tree"]
    state.ingestion_warnings = ["Guide non esaminate"]
    setattr(state, field, [])

    with pytest.raises(ValueError):
        validate_ingestion_snapshot(state, settings)


def test_truncated_tree_and_file_warnings_survive_mapping(valid_snapshot):
    state, settings = valid_snapshot
    result = InspectRepositoryResult(
        status="partial",
        repository_path=state.local_repo_path,
        inspected_commit_sha="a" * 40,
        inspected_branch="main",
        inspected_sections=["tree", "guides"],
        repository_tree_truncated=True,
        repository_guides={"README.md": "partial"},
        truncated_files=["README.md"],
        warnings=["Albero e README troncati"],
    )
    for field, value in inspection_checkout_update(result).items():
        setattr(state, field, value)

    assert state.ingestion_warnings == ["Albero e README troncati"]
    assert state.truncated_files == ["README.md"]
    assert route_after_ingestion(state, settings=settings) == "completed"


@pytest.mark.parametrize(
    ("state_updates", "setting_updates", "error"),
    [
        ({"repository_tree": ["a.py", "b.py"]},
         {"max_repository_tree_entries": 1}, "Troppi path"),
        ({"repository_tree": ["src/deep/a.py"]},
         {"max_repository_tree_depth": 2}, "Profondità"),
        ({"repository_guides": {"README.md": "abc"}},
         {"max_guide_chars": 2}, "Guida oltre"),
        ({"repository_guides": {"README.md": "éé"}},
         {"max_repository_file_bytes": 3}, "limite di byte"),
        ({"repository_guides": {"README.md": "abc"},
          "project_manifests": {"pyproject.toml": "de"}},
         {"max_ingestion_total_chars": 4}, "budget totale"),
    ],
)
def test_snapshot_respects_trusted_quantity_and_size_budgets(
    valid_snapshot, state_updates, setting_updates, error,
):
    state, settings = valid_snapshot
    for field, value in state_updates.items():
        setattr(state, field, value)
    settings = settings.model_copy(update=setting_updates)

    with pytest.raises(ValueError, match=error):
        validate_ingestion_snapshot(state, settings)


def test_state_revalidates_paths_even_after_direct_update(valid_snapshot):
    state, settings = valid_snapshot
    state.repository_tree = ["../secret"]

    with pytest.raises(ValueError, match="Path relativo"):
        validate_ingestion_snapshot(state, settings)


def test_snapshot_accepts_exact_budget_boundaries(valid_snapshot):
    state, settings = valid_snapshot
    state.repository_tree = ["README.md", "pyproject.toml"]
    state.repository_guides = {"README.md": "é"}
    state.project_manifests = {"pyproject.toml": "x"}
    settings = settings.model_copy(update={
        "max_repository_tree_entries": 2,
        "max_repository_tree_depth": 1,
        "max_guide_chars": 1,
        "max_repository_file_bytes": 2,
        "max_ingestion_total_chars": 2,
    })

    validate_ingestion_snapshot(state, settings)
