from __future__ import annotations
from functools import partial
from agent.deps import load_llms_from_env
from typing import Optional
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.constants import START, END, CompiledStateGraph, StateGraph
from src.agent.state import BugFixingState
from src.agent.nodes.discovery import discovery_node as discovery 
from src.agent.nodes.ingestion import ingestion_node as ingestion
from agent.nodes.advisory import advisory_node as advisor
import os 

class SherpaAgent:
    '''
    A class that represents an agent for fixing bugs in a codebase.
    It uses a graph representation of the codebase to identify and fix bugs based on a given bug description.
    '''

    def __init__(self, repo_url: str, bug_description: str, tools_by_name: list):
        self.repo_url = repo_url
        self.bug_description = bug_description
        self._graph = Optional[CompiledStateGraph] = None
        self.tools_by_name = [tools for tools in tools_by_name]
        self.llm = ChatGoogleGenerativeAI(model="gemini-3.1-lite")



    def create_graph(self):
        """
        Create a graph representation of the codebase.
        This method should initialize the graph structure and populate it with nodes and edges.
        """
        # Implementation for creating the graph
        deps = load_llms_from_env()
        state = BugFixingState()

        graph = StateGraph(state)

        graph.add_node("discovery", partial(discovery, llm=deps.discovery))
        graph.add_node("ingestion", partial(ingestion, llm=deps.ingestion))
        graph.add_node("advisory", partial(advisor, llm=deps.advisory))

        graph.add_edge(START, "discovery")
        graph.add_edge("discovery", "ingestion")
        graph.add_edge("ingestion", "advisory")
        graph.add_edge("advisory",  END)

        self._graph = graph.compile()
    
    def run(self):
        """
        Run the bug fixing process: analyze the codebase, identify the bug location, suggest a fix, and apply it.
        """
        pass
 