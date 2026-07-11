from langchain_google_genai import ChatGoogleGenerativeAI
from agent.state import BugFixingState


def advisory_node(state: BugFixingState, llm: ChatGoogleGenerativeAI):
    """
    This function represents the advisory node in the bug fixing process.
    It is responsible for providing advice or suggestions on how to fix the identified bug.
    """
    # Implementation for the advisory node
    print("Advisory node invoked with state:", state)
    pass