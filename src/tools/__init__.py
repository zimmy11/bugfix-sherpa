"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes tools for web search
and other external integrations.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .fs_read import fs_read_file
from .github import github_tool

tools: list[BaseTool] = [fs_read_file, ask_human, github_tool]