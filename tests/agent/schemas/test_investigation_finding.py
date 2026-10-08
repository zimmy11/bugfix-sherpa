"""Required evidence content and numeric bounds for findings."""

import pytest
from pydantic import ValidationError

from src.agent.schema import (
    CodeFindingSource,
    InvestigationFinding,
    LogFindingSource,
    MAX_LOG_EXCERPT_CHARS,
)


def test_empty_finding_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        InvestigationFinding()

    missing_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert {
        "source", "observation", "interpretation", "confidence", "confidence_reason",
    } <= missing_fields


@pytest.mark.parametrize("line", [None, 0, -1, True, 1.5, "12"])
def test_line_must_be_positive(finding_data, line):
    finding_data["source"]["line"] = line

    with pytest.raises(ValidationError) as exc_info:
        InvestigationFinding.model_validate(finding_data)

    assert any(error["loc"] == ("source", "code", "line") for error in exc_info.value.errors())


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_unit_interval_is_rejected(finding_data, confidence):
    finding_data["confidence"] = confidence

    with pytest.raises(ValidationError) as exc_info:
        InvestigationFinding.model_validate(finding_data)

    assert any(
        error["loc"] == ("confidence",) for error in exc_info.value.errors()
    )


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_confidence_boundary_values_are_valid(finding_data, confidence):
    finding_data["confidence"] = confidence

    finding = InvestigationFinding.model_validate(finding_data)

    assert finding.confidence == confidence
    assert finding.source.line == 12


@pytest.mark.parametrize("source", [None, {}, {"type": "unknown"}])
def test_finding_requires_a_discriminated_source(finding_data, source):
    finding_data["source"] = source
    with pytest.raises(ValidationError):
        InvestigationFinding.model_validate(finding_data)


def test_content_without_source_is_rejected(finding_data):
    finding_data.pop("source")
    # Legacy coordinates must not be silently discarded or accepted as evidence.
    finding_data.update(file="src/example.py", line=12)
    with pytest.raises(ValidationError):
        InvestigationFinding.model_validate(finding_data)


@pytest.mark.parametrize(
    "path",
    [
        "", "  ", "/src/a.py", "../a.py", "src/../a.py", "./a.py",
        "src/./a.py", "src//a.py", "src/", "C:/src/a.py",
        "src\\a.py", "//server/a.py", "src/\x00a.py", "src/\na.py",
        "src/\x7fa.py", "src/ /a.py", "https://example.com/a.py",
    ],
)
def test_code_source_rejects_disallowed_paths(finding_data, path):
    finding_data["source"]["file"] = path
    with pytest.raises(ValidationError):
        InvestigationFinding.model_validate(finding_data)


@pytest.mark.parametrize("field", ["file", "line"])
def test_code_source_requires_both_coordinates(finding_data, field):
    finding_data["source"].pop(field)
    with pytest.raises(ValidationError):
        InvestigationFinding.model_validate(finding_data)


@pytest.mark.parametrize("field", ["observation", "interpretation", "confidence_reason"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_finding_rejects_blank_required_text(finding_data, field, value):
    finding_data[field] = value
    with pytest.raises(ValidationError):
        InvestigationFinding.model_validate(finding_data)


@pytest.mark.parametrize(
    "source",
    [
        {"type": "log", "excerpt": "failure"},
        {"type": "log", "execution_id": "runner-42"},
        {"type": "log", "execution_id": " ", "excerpt": "failure"},
        {"type": "log", "execution_id": "runner-42", "excerpt": " \n"},
        {
            "type": "log", "execution_id": "runner-42",
            "excerpt": "x" * (MAX_LOG_EXCERPT_CHARS + 1),
        },
        {"type": "code", "file": "src/a.py", "line": 1, "excerpt": "failure"},
        {"type": "log", "execution_id": "runner-42", "excerpt": "failure", "line": 1},
    ],
)
def test_sources_reject_incomplete_or_mixed_provenance(finding_data, source):
    finding_data["source"] = source
    with pytest.raises(ValidationError):
        InvestigationFinding.model_validate(finding_data)


@pytest.mark.parametrize("size", [1, MAX_LOG_EXCERPT_CHARS])
def test_log_source_preserves_a_bounded_execution_excerpt(finding_data, size):
    finding_data["source"] = {
        "type": "log", "execution_id": "runner-42", "excerpt": "x" * size,
    }
    finding = InvestigationFinding.model_validate(finding_data)
    restored = InvestigationFinding.model_validate_json(finding.model_dump_json())
    assert isinstance(restored.source, LogFindingSource)
    assert restored.source.execution_id == "runner-42"
    assert restored.source.excerpt == "x" * size
    assert restored == finding


def test_code_source_shape_does_not_require_an_existing_file(finding_data):
    finding_data["source"] = {
        "type": "code", "file": "not-a-real-checkout/missing.py", "line": 10000,
    }
    finding = InvestigationFinding.model_validate(finding_data)
    restored = InvestigationFinding.model_validate_json(finding.model_dump_json())
    assert isinstance(restored.source, CodeFindingSource)
    assert restored == finding


def test_json_schema_exposes_the_source_discriminator():
    schema = InvestigationFinding.model_json_schema()
    assert schema["properties"]["source"]["discriminator"]["propertyName"] == "type"
    assert "source" in schema["required"]
