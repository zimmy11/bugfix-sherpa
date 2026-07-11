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
from src.tools import tools
from IPython.display import Image, display

class SherpaAgent:
    '''
    A class that represents an agent for fixing bugs in a codebase.
    It uses a graph representation of the codebase to identify and fix bugs based on a given bug description.
    '''

    def __init__(self):
        self._graph : Optional[CompiledStateGraph] | None = None
        self.tools = {tool.name: tool for tool in tools}



    def create_graph(self):
        """
        Create a graph representation of the codebase.
        This method should initialize the graph structure and populate it with nodes and edges.
        """
        # Implementation for creating the graph
        deps = LLMNodesConfig.load_llms_from_env()
        graph = StateGraph(BugFixingState)

        graph.add_node("discovery", partial(discovery, llm=deps.discovery))
        graph.add_node("ingestion", partial(ingestion, llm=deps.ingestion))
        graph.add_node("advisory", partial(advisor, llm=deps.advisory))

        graph.add_edge(START, "discovery")
        graph.add_edge("discovery", "ingestion")
        graph.add_edge("ingestion", "advisory")
        graph.add_edge("advisory",  END)

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
 