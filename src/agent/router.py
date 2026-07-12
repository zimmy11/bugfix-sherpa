from src.agent.graph import BugFixingState

def route_after_triage(state: BugFixingState) -> str:
    if state.messages:
        last_message = state.messages[-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"

    if state.triage_status in ["accepted", "rejected"]:
        return state.triage_status
    raise ValueError(f"triage_status non valido: {state.triage_status!r}")

def route_after_ingestion(state: BugFixingState) -> str:
    if state.messages:
        last_message = state.messages[-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"

    return "completed"

def route_after_discovery(state: BugFixingState) -> str:
    if not state.messages:
        return "completed"

    last_message = state.messages[-1]

    if getattr(last_message, "tool_calls", None):
        return "tools"

    return "completed"
