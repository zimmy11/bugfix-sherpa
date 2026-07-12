
from langchain_google_genai import ChatGoogleGenerativeAI
from src.utils.config import Settings
from src.agent.state import BugFixingState
from src.prompts.discovery import DISCOVERY_SYSTEM_PROMPT as SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, HumanMessage

def discovery_node(state: BugFixingState, llm: ChatGoogleGenerativeAI, settings: Settings):
    """
    This function represents the discovery node in the bug fixing process.
    It is responsible for identifying and discovering bugs in the codebase.
    """
    # Implementation for the discovery node
    messages = [
    SystemMessage(
        content=SYSTEM_PROMPT
    ),
    *state.messages,
    ]

    response = llm.invoke(messages)
    print(f"Attraversamento Nodo Discovery con stato {state}")
    return {
        "messages": [response],
        # "issue_candidates": candidates
        "current_node": "discovery",
    }    