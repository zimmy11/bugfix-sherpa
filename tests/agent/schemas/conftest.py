"""Valid input data shared by the schema tests."""

import pytest


@pytest.fixture
def finding_data():
    return {
        "source": {"type": "code", "file": "src/example.py", "line": 12},
        "observation": "The empty input reaches the indexing operation.",
        "interpretation": "The missing guard may explain the reported error.",
        "confidence": 0.8,
        "confidence_reason": "The code matches the failure described in the issue.",
    }


@pytest.fixture
def report_data():
    return {
        "repository_full_name": "owner/project",
        "issue_number": 42,
        "issue_url": "https://github.com/owner/project/issues/42",
        "branch": "main",
        "commit_sha": "a" * 40,
        "problem_summary": "Empty input produces an unexpected error.",
        "investigation_status": "inconclusive",
        "investigation_limits": ["The target repository tests were not run."],
    }
