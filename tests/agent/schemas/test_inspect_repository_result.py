"""Identity required from a completed or partial repository inspection."""

import pytest
from pydantic import ValidationError

from src.agent.schema import InspectRepositoryResult


@pytest.mark.parametrize("status", ("completed", "partial"))
def test_successful_inspection_requires_checkout_identity(tmp_path, status):
    data = {
        "status": status,
        "repository_path": str(tmp_path / "checkout"),
        "inspected_commit_sha": "a" * 40,
        "inspected_branch": "main",
        "inspected_sections": (
            ["tree", "guides", "manifests", "test_config"]
            if status == "completed" else ["tree"]
        ),
        "warnings": [] if status == "completed" else ["Partial scan"],
    }
    result = InspectRepositoryResult.model_validate(data)

    assert InspectRepositoryResult.model_validate_json(result.model_dump_json()) == result

    for missing_field in (
        "repository_path", "inspected_commit_sha", "inspected_branch"
    ):
        with pytest.raises(ValidationError):
            InspectRepositoryResult.model_validate({
                **data,
                missing_field: None,
            })


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_path", "relative/checkout"),
        ("repository_path", "   "),
        ("inspected_commit_sha", "not-a-sha"),
        ("inspected_branch", "bad..branch"),
        ("inspected_branch", "   "),
    ],
)
def test_invalid_inspection_identity_is_rejected(tmp_path, field, value):
    data = {
        "status": "completed",
        "repository_path": str(tmp_path / "checkout"),
        "inspected_commit_sha": "a" * 40,
        "inspected_branch": "main",
        "inspected_sections": ["tree", "guides", "manifests", "test_config"],
        field: value,
    }

    with pytest.raises(ValidationError):
        InspectRepositoryResult.model_validate(data)


def test_failed_inspection_can_omit_checkout_identity():
    result = InspectRepositoryResult(status="failed")

    assert result.repository_path is None
    assert result.inspected_commit_sha is None
    assert result.inspected_branch is None


def _inspection_data(tmp_path, status="partial"):
    return {
        "status": status,
        "repository_path": str(tmp_path / "checkout"),
        "inspected_commit_sha": "a" * 40,
        "inspected_branch": "main",
        "inspected_sections": ["tree"] if status == "partial" else [
            "tree", "guides", "manifests", "test_config"
        ],
        "warnings": ["Scan incomplete"] if status == "partial" else [],
    }


def test_partial_accepts_scanned_but_empty_tree(tmp_path):
    result = InspectRepositoryResult.model_validate(_inspection_data(tmp_path))

    assert result.status == "partial"
    assert result.repository_tree == []
    assert result.repository_tree_truncated is False


@pytest.mark.parametrize("missing_field", ("inspected_sections", "warnings"))
def test_partial_requires_scanned_section_and_warning(tmp_path, missing_field):
    data = _inspection_data(tmp_path)
    data[missing_field] = []

    with pytest.raises(ValidationError):
        InspectRepositoryResult.model_validate(data)


def test_completed_accepts_empty_collections_after_all_sections_scanned(tmp_path):
    result = InspectRepositoryResult.model_validate(
        _inspection_data(tmp_path, status="completed")
    )

    assert result.repository_tree == []
    assert result.repository_guides == {}
    assert result.project_manifests == {}
    assert result.test_config_files == []


def test_completed_requires_all_sections_regardless_of_order(tmp_path):
    data = _inspection_data(tmp_path, status="completed")
    data["inspected_sections"] = list(reversed(data["inspected_sections"]))
    assert InspectRepositoryResult.model_validate(data).status == "completed"

    data["inspected_sections"].remove("guides")
    with pytest.raises(ValidationError, match="senza tutte le sezioni"):
        InspectRepositoryResult.model_validate(data)


def test_duplicate_inspected_sections_are_rejected(tmp_path):
    data = _inspection_data(tmp_path)
    data["inspected_sections"] = ["tree", "tree"]

    with pytest.raises(ValidationError, match="sezioni duplicate"):
        InspectRepositoryResult.model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"inspected_sections": ["guides"], "repository_tree_truncated": True},
        {"warnings": ["  "], "repository_tree_truncated": True},
        {"status": "completed", "repository_tree_truncated": True},
    ],
)
def test_truncated_tree_requires_scanned_tree_warning_and_partial_status(
    tmp_path, changes,
):
    data = _inspection_data(tmp_path)
    if changes.get("status") == "completed":
        data["inspected_sections"] = [
            "tree", "guides", "manifests", "test_config"
        ]
    data.update(changes)

    with pytest.raises(ValidationError):
        InspectRepositoryResult.model_validate(data)


def test_partial_tree_truncation_is_distinct_from_empty_tree(tmp_path):
    data = _inspection_data(tmp_path)
    data["repository_tree_truncated"] = True

    result = InspectRepositoryResult.model_validate(data)

    assert result.repository_tree == []
    assert result.repository_tree_truncated is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_tree", ["../secret"]),
        ("repository_tree", ["a.py", "a.py"]),
        ("repository_guides", {"C:/secret": "text"}),
        ("project_manifests", {"/etc/passwd": "text"}),
        ("test_config_files", ["tests\\pytest.ini"]),
        ("test_config_files", ["pytest.ini", "pytest.ini"]),
    ],
)
def test_exposed_paths_must_be_unique_relative_posix_paths(
    tmp_path, field, value,
):
    data = _inspection_data(tmp_path, status="completed")
    data[field] = value

    with pytest.raises(ValidationError):
        InspectRepositoryResult.model_validate(data)


def test_partial_cannot_expose_data_from_unscanned_section(tmp_path):
    data = _inspection_data(tmp_path)
    data["repository_guides"] = {"README.md": "text"}

    with pytest.raises(ValidationError, match="sezione non ispezionata"):
        InspectRepositoryResult.model_validate(data)


def test_truncated_file_must_be_present_and_have_warning(tmp_path):
    data = _inspection_data(tmp_path)
    data["inspected_sections"] = ["guides"]
    data["repository_guides"] = {"README.md": "partial text"}
    data["truncated_files"] = ["README.md"]
    assert InspectRepositoryResult.model_validate(data).truncated_files == [
        "README.md"
    ]

    data["truncated_files"] = ["OTHER.md"]
    with pytest.raises(ValidationError, match="non è presente"):
        InspectRepositoryResult.model_validate(data)


def test_completed_cannot_contain_truncated_file(tmp_path):
    data = _inspection_data(tmp_path, status="completed")
    data["repository_guides"] = {"README.md": "partial"}
    data["truncated_files"] = ["README.md"]
    data["warnings"] = ["README troncato"]

    with pytest.raises(ValidationError, match="non può essere troncata"):
        InspectRepositoryResult.model_validate(data)
