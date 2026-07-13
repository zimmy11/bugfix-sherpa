
from langchain_google_genai import ChatGoogleGenerativeAI
from src.utils.config import Settings
from src.agent.state import BugFixingState


def ingestion_node(state : BugFixingState, llm: ChatGoogleGenerativeAI, settings: Settings):
    """
    This function represents the ingestion node in the bug fixing process.
    It is responsible for ingesting and processing the codebase to identify potential bugs.
    """
    # Implementation for the ingestion node
    print(f"Attraversamento Nodo Ingestion con stato: {state.selected_issue}, {state.issue_comments}, {state.issue_body}")
    pass