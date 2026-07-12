"""Human-in-the-loop confirmation tool for LangGraph.

This module provides a tool that pauses graph execution to ask the user
for confirmation before proceeding with a sensitive action.
"""

from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def ask_human(question: str) -> str:
    """Pause graph execution and request a decision from the human reviewer.

    Use this tool only when an LLM-driven node genuinely needs clarification
    before continuing. For Bugfix Sherpa's mandatory final review, prefer a
    deterministic Human Review node that calls ``interrupt`` directly instead
    of allowing an LLM to decide whether the pause happens.

    Args:
        question: A specific question that includes the relevant context and
            clearly states which decision or missing information is required.

    Returns:
        The human response supplied when the interrupted graph is resumed.
    """
    user_response = interrupt(question)
    return str(user_response)
