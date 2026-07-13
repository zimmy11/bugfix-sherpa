from langchain_google_genai import ChatGoogleGenerativeAI
from src.agent.state import BugFixingState
from src.utils.config import Settings

def advisory_node(state: BugFixingState, llm: ChatGoogleGenerativeAI, settings: Settings):
    """
    This function represents the advisory node in the bug fixing process.
    It is responsible for providing advice or suggestions on how to fix the identified bug.
    """
    # Implementation for the advisory node
    print(f"Attraversamento Nodo Advisory con stato")
    pass