"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes tools for web search
and other external integrations.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .fs_read import fs_read_file

ToolReference = str | BaseTool

discovery_tools: list[ToolReference] = [
    "search_github_issues",
]

triage_tools: list[ToolReference] = [
    "read_issue_thread",
]

ingestion_tools: list[ToolReference] = [
    fs_read_file,
]

human_review_tools: list[ToolReference] = [
    ask_human,
]

# GitHub tool names are resolved to client-bound tool instances in create_graph.
tools: dict[str, list[ToolReference]] = {
    "discovery": discovery_tools,
    "triage": triage_tools,
    "ingestion": ingestion_tools,
    "human_review": human_review_tools
}
