
from langchain_google_genai import ChatGoogleGenerativeAI
from src.utils.config import Settings
from src.agent.state import BugFixingState
from src.prompts.discovery import DISCOVERY_SYSTEM_PROMPT as SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
import json 


def _message_content(message: ToolMessage):
    """Restituisce il contenuto del tool come oggetto Python."""
    if isinstance(message.content, str):
        return json.loads(message.content)
    return message.content

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
    print(f"Attraversamento Nodo Discovery con stato")

    
    all_tool_calls = []

    for message in reversed(state.messages):
        if not isinstance(message, ToolMessage):
            continue

        all_tool_calls.append(message)
    candidates = list(state.issue_candidates)
    for tool_call in all_tool_calls:
        if tool_call.name == "search_github_issues":
            content = _message_content(tool_call)

            if isinstance(content, dict):
                content = content.get("candidates", [])

            if not isinstance(content, list):
                continue
            candidates.extend(
                candidate
                for candidate in content
                if isinstance(candidate, dict)
            )

    return {
        "messages": [response],
        "issue_candidates": candidates,
        "current_node": "discovery",
    }
