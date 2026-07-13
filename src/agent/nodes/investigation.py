from langchain_google_genai import ChatGoogleGenerativeAI
from src.agent.state import BugFixingState
from src.utils.config import Settings


def node_investigation( state: BugFixingState, llm: ChatGoogleGenerativeAI, settings: Settings):
    """
    A node in the graph that investigates the bug based on the current state.
    It uses a language model (llm) to analyze the bug description and suggest potential fixes.
    """

    print(f"Attraversamento Nodo Investigation con stato")
    pass
