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
from src.utils.config import Settings

class SherpaAgent:
    '''
    A class that represents an agent for fixing bugs in a codebase.
    It uses a graph representation of the codebase to identify and fix bugs based on a given bug description.
    '''

    def __init__(self, settings: Settings):
        self._graph : Optional[CompiledStateGraph] | None = None
        self.tools = {tool.name: tool for tool in tools}
        self.settings = settings
        self.deps = LLMNodesConfig.from_settings(settings=settings)

    def route_after_triage(self, state: BugFixingState) -> str:
        if state.triage_status in ["accepted", "rejected"]:
            return state.triage_status
        raise ValueError(f"triage_status non valido: {state.triage_status!r}")


    def create_graph(self):
        """
        Create a graph representation of the codebase.
        This method should initialize the graph structure and populate it with nodes and edges.
        """
        # Implementation for creating the graph
        graph = StateGraph(BugFixingState)

        graph.add_node("discovery", partial(discovery, llm=self.deps.discovery))
        graph.add_node("ingestion", partial(ingestion, llm=self.deps.ingestion))
        graph.add_node("advisory", partial(advisor, llm=self.deps.advisory))
        graph.add_node("triage", partial(triage, llm=self.deps.triage))
        graph.add_node("investigation", partial(investigation, llm=self.deps.investigation))
        graph.add_node("human_review", human_review)
        graph.add_edge(START, "discovery")
        graph.add_edge("discovery", "triage")
        graph.add_conditional_edges("triage", self.route_after_triage,
        {
            "accepted": "ingestion",
            "rejected": "discovery",
            },)
        graph.add_edge("ingestion", "investigation")
        graph.add_edge("investigation", "advisory")
        graph.add_edge("advisory", "human_review")
        graph.add_edge("human_review",  END)

        self._graph = graph.compile()
        png_data = self._graph.get_graph().draw_mermaid_png()

        with open("src/agent/graph.png", "wb") as f:
            f.write(png_data)

    def run(self):
        """
        Run the bug fixing process: analyze the codebase, identify the bug location, suggest a fix, and apply it.
        """
        if self._graph is None:
            self.create_graph()
        self._graph.invoke(BugFixingState())
 