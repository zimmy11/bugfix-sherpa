from langchain_google_genai import ChatGoogleGenerativeAI
from src.agent.state import BugFixingState
from src.utils.config import Settings

def triage_node(state: BugFixingState, llm: ChatGoogleGenerativeAI, settings: Settings):
    """
    A node in the graph that performs triage on the bug based on the current state.
    It uses a language model (llm) to analyze the bug description and suggest potential fixes.
    """

    print(f"Attraversamento Nodo Triage con stato {state}")
    return {"triage_status": "accepted"}