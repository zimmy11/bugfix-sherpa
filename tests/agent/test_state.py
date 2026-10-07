"""Input and counter bounds for the workflow state."""

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.agent.state import BugFixingState, investigation_collection_update


@pytest.mark.parametrize("field", ("ingestion_attempts", "retry_count"))
def test_counters_start_at_zero(field):
    assert getattr(BugFixingState(), field) == 0


@pytest.mark.parametrize("field", ("ingestion_attempts", "retry_count"))
@pytest.mark.parametrize("value", (-1, -2))
def test_negative_counters_are_rejected(field, value):
    with pytest.raises(ValidationError) as exc_info:
        BugFixingState(**{field: value})

    assert any(error["loc"] == (field,) for error in exc_info.value.errors())


@pytest.mark.parametrize("value", (1, 100))
def test_max_results_accepts_boundaries(value):
    assert BugFixingState(max_results=value).max_results == value


@pytest.mark.parametrize("value", (0, -1, 101))
def test_max_results_rejects_out_of_range(value):
    with pytest.raises(ValidationError) as exc_info:
        BugFixingState(max_results=value)

    assert any(error["loc"] == ("max_results",) for error in exc_info.value.errors())


def test_initial_state_has_valid_result_limit():
    assert 1 <= BugFixingState().max_results <= 100


def test_repeated_investigation_snapshot_does_not_duplicate_collections():
    snapshot = {
        "files_analyzed": ["src/parser.py"],
        "search_results": [{"file": "src/parser.py", "line": 12, "query": "parse"}],
        "relevant_symbols": [{"file": "src/parser.py", "symbol": "parse", "line": 10}],
        "error_signatures": ["ValueError: invalid input"],
    }
    graph = StateGraph(BugFixingState)
    graph.add_node("first", lambda state: snapshot)
    graph.add_node("repeated", lambda state: snapshot)
    graph.add_edge(START, "first")
    graph.add_edge("first", "repeated")
    graph.add_edge("repeated", END)

    final_state = graph.compile().invoke(BugFixingState())

    for field, expected in snapshot.items():
        assert final_state[field] == expected


def test_investigation_update_returns_complete_deduplicated_collections():
    graph = StateGraph(BugFixingState)
    graph.add_node(
        "first",
        lambda state: investigation_collection_update(
            state,
            files_analyzed=["src/parser.py"],
            search_results=[{
                "file": "src/parser.py", "line": 12, "query": "parse",
                "snippet": "old",
            }],
            relevant_symbols=[{
                "file": "src/parser.py", "symbol": "parse", "line": 10,
            }],
            error_signatures=["ValueError: invalid input"],
        ),
    )
    graph.add_node(
        "second",
        lambda state: investigation_collection_update(
            state,
            files_analyzed=["src/parser.py", "src/lexer.py"],
            search_results=[{
                "file": "src/parser.py", "line": 12, "query": "parse",
                "snippet": "new",
            }],
            relevant_symbols=[{
                "file": "src/parser.py", "symbol": "parse", "line": 10,
                "detail": "confirmed",
            }],
            error_signatures=["  valueerror:   INVALID input  ", "TypeError"],
        ),
    )
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    final_state = graph.compile().invoke(BugFixingState())

    assert final_state["files_analyzed"] == ["src/parser.py", "src/lexer.py"]
    assert final_state["search_results"] == [{
        "file": "src/parser.py", "line": 12, "query": "parse",
        "snippet": "new",
    }]
    assert final_state["relevant_symbols"] == [{
        "file": "src/parser.py", "symbol": "parse", "line": 10,
        "detail": "confirmed",
    }]
    assert final_state["error_signatures"] == [
        "  valueerror:   INVALID input  ", "TypeError"
    ]


def test_investigation_update_rejects_missing_stable_key():
    with pytest.raises(ValueError, match="campo chiave: query"):
        investigation_collection_update(
            BugFixingState(), search_results=[{"file": "src/a.py", "line": 1}]
        )
