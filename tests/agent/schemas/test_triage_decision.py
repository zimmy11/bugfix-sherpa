"""Decision validation independently of GitHub and the LLM."""

import pytest
from pydantic import ValidationError

from src.agent.schema import TriageDecision


@pytest.mark.parametrize("issue_key", [None, "", "   "])
def test_accepted_requires_an_issue_key(issue_key):
    with pytest.raises(ValidationError, match="accepted richiede issue_key"):
        TriageDecision(
            status="accepted", reason="The issue can be investigated.",
            issue_key=issue_key,
        )


def test_rejected_cannot_select_an_issue():
    with pytest.raises(ValidationError, match="rejected non seleziona issue"):
        TriageDecision(
            status="rejected", reason="The issue is already assigned.",
            issue_key="owner/project#42",
        )


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_decision_requires_a_nonempty_reason(reason):
    with pytest.raises(ValidationError):
        TriageDecision(status="rejected", reason=reason)


@pytest.mark.parametrize(
    ("status", "issue_key"),
    [("accepted", "owner/project#42"), ("rejected", None)],
)
def test_valid_decision_can_be_read_from_json(status, issue_key):
    decision = TriageDecision(
        status=status, reason="Decision based on the issue thread.",
        issue_key=issue_key,
    )

    restored = TriageDecision.model_validate_json(decision.model_dump_json())

    assert restored.status == status
    assert restored.issue_key == issue_key
