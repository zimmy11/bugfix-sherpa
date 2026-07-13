from __future__ import annotations
from functools import partial
from src.agent.deps import LLMNodesConfig
from typing import Optional
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from src.agent.state import BugFixingState
from src.agent.nodes.discovery import discovery_node as discovery 
from src.agent.nodes.ingestion import ingestion_node as ingestion
from src.agent.nodes.advisory import advisory_node as advisor
from src.agent.nodes.triage import triage_node as triage
from src.agent.nodes.investigation import node_investigation as investigation
from src.agent.nodes.human_review import human_review_node as human_review
from src.tools import tools
from src.tools.github import build_github_tools
from src.utils.config import Settings
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy
from github import Github
from langchain_core.tools import BaseTool
from src.agent.router import (
    route_after_discovery,
    route_after_ingestion,
    route_after_triage,
)


class SherpaAgent:
    '''
    A class that represents an agent for fixing bugs in a codebase.
    It uses a graph representation of the codebase to identify and fix bugs based on a given bug description.
    '''

    def __init__(self, settings: Settings, github_client: Github):
        self._graph : Optional[CompiledStateGraph] | None = None
        self.tools = tools
        self.settings = settings
        self.deps = LLMNodesConfig.from_settings(settings=settings)
        self.github_client = github_client

    def _resolve_tool_group(
        self,
        group_name: str,
        configured_tools: dict[str, BaseTool],
    ) -> list[BaseTool]:
        """Resolve a declared tool group to executable tool instances."""
        resolved_tools: list[BaseTool] = []

        for reference in self.tools[group_name]:
            if isinstance(reference, str):
                try:
                    resolved_tools.append(configured_tools[reference])
                except KeyError as error:
                    raise ValueError(
                        f"Tool {reference!r} non configurato per il gruppo "
                        f"{group_name!r}"
                    ) from error
            else:
                resolved_tools.append(reference)

        return resolved_tools


    def create_graph(self):
        """
        Create a graph representation of the codebase.
        This method should initialize the graph structure and populate it with nodes and edges.
        """
        # Implementation for creating the graph



        graph = StateGraph(BugFixingState)
        tool_retry_policy = RetryPolicy(
            initial_interval=self.settings.api_retry_min_seconds,
            backoff_factor=2.0,
            max_interval=self.settings.api_retry_max_seconds,
            max_attempts=3,
            jitter=True,
        )
        github_tools = build_github_tools(
            self.github_client,
            discovery_queries=self.settings.github_queries,
            limit=self.settings.github_max_results,
            min_stars=self.settings.github_min_stars,
            repository_inactivity_days=(
                self.settings.repository_inactivity_days
            ),
        )

        discovery_tools = self._resolve_tool_group(
            "discovery", github_tools
        )
        triage_tools = self._resolve_tool_group(
            "triage", github_tools
        )
        ingestion_tools = self._resolve_tool_group(
            "ingestion", github_tools
        )

        discovery_llm = self.deps.discovery.bind_tools(discovery_tools)
        ingestion_llm = self.deps.ingestion.bind_tools(ingestion_tools)
        triage_llm = self.deps.triage.bind_tools(triage_tools)

        graph.add_node("discovery", partial(discovery, llm=discovery_llm, settings = self.settings))
        graph.add_node(
            "discovery_tools",
            ToolNode(discovery_tools),
            retry_policy=tool_retry_policy,
        )

        graph.add_node("ingestion", partial(ingestion, llm=ingestion_llm, settings = self.settings))
        graph.add_node("ingestion_tools", ToolNode(ingestion_tools))

        graph.add_node("advisory", partial(advisor, llm=self.deps.advisory , settings = self.settings))
        
        graph.add_node("triage", partial(triage, llm=triage_llm, settings = self.settings))
        graph.add_node(
            "triage_tools",
            ToolNode(triage_tools),
            retry_policy=tool_retry_policy,
        )

        graph.add_node("investigation", partial(investigation, llm=self.deps.investigation, settings = self.settings))
        graph.add_node("human_review", human_review)


        graph.add_edge(START, "discovery")
        graph.add_conditional_edges("discovery", route_after_discovery, {"tools": "discovery_tools", "completed": "triage"})
        graph.add_edge("discovery_tools", "discovery")
        graph.add_conditional_edges("triage", route_after_triage,
        {
            "accepted": "ingestion",
            "rejected": END,
            "tools": "triage_tools"
            },)
        graph.add_edge("triage_tools", "triage")
        graph.add_conditional_edges(
            "ingestion",
            route_after_ingestion,
            {
                "tools": "ingestion_tools",
                "completed": "investigation",
            },
        )
        graph.add_edge("ingestion_tools", "ingestion")
        graph.add_edge("investigation", "advisory")
        graph.add_edge("advisory", "human_review")
        graph.add_edge("human_review",  END)

        self._graph = graph.compile()
        png_data = self._graph.get_graph().draw_mermaid_png()

        with open("src/agent/graph.png", "wb") as f:
            f.write(png_data)

    def run(self, state: BugFixingState):
        """
        Run the bug fixing process: analyze the codebase, identify the bug location, suggest a fix, and apply it.
        """
        if self._graph is None:
            self.create_graph()
        self._graph.invoke(state)
