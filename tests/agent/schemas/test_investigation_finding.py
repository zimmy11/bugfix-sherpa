"""Required evidence content and numeric bounds for findings."""

import pytest
from pydantic import ValidationError

from src.agent.schema import InvestigationFinding


def test_empty_finding_is_rejected():
    with pytest.raises(ValidationError) as exc_info:
        InvestigationFinding()

    missing_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert {
        "observation", "interpretation", "confidence", "confidence_reason",
    } <= missing_fields


@pytest.mark.parametrize("line", [0, -1])
def test_line_must_be_positive(finding_data, line):
    finding_data["line"] = line

    with pytest.raises(ValidationError) as exc_info:
        InvestigationFinding.model_validate(finding_data)

    assert any(error["loc"] == ("line",) for error in exc_info.value.errors())


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
    assert finding.line == 12
